#!/usr/bin/env python3
"""prompt-lab v3 · Phase D smoke + Phase E main run.

Modes:
    python3 -u run_round.py <workspace> --smoke
        Phase D: POST /chat single dialogue (first persona × max_turns=4).
        Writes <workspace>/prompts/<id>/iterations/round-NN/smoke/transcripts.jsonl.
        Reports per-turn latency_ms vs worker_timeout × 0.7 threshold.

    python3 -u run_round.py <workspace> --round round-01
        Phase E: POST /simulation per persona, count=K.
        Writes <workspace>/prompts/<id>/iterations/round-NN/transcripts.jsonl.
        Resumable: skips persona_ids that already have full K results.

Reads <workspace>/config.json for:
    remote_server, access_token, worker_timeout_seconds,
    agent_a / agent_b / end_checker (network_mode + request),
    runtime, greeting, repeats_per_persona, concurrency.

Reads <workspace>/prompts/<id>/personas/pool.jsonl + iterations/<round>/prompt.md.

Compatible with dispatcher schema 2026-05+ (x-access-token, proxy/network, status=succeeded, result.history).

Deps: stdlib only. network_mode helper is sibling file.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# import network_mode from same dir
sys.path.insert(0, str(Path(__file__).parent))
from network_mode import role_network_block, apply_quirks_to_request, is_pro_model_blocked  # noqa: E402


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


ASR_NOISE_BLOCKS = {
    "light": "\n[额外指令] 模拟轻度 ASR 失真：~10% 概率把数字读错一两位、同音字说混，仅 1-2 类失真。\n",
    "moderate": "\n[额外指令] 模拟中等 ASR 失真：~25% 概率出现同音字误识/漏字/增字，数字读法偶混乱，3 类失真混合。\n",
    "heavy": "\n[额外指令] 模拟重度 ASR 失真：~40% 句子有显著失真，同音字+漏字+数字漂移+断句错位复合出现，但说话意图始终一致。\n",
}


def build_user_system_prompt(persona: dict) -> str:
    base = persona.get("prompt", "")
    level = persona.get("asr_noise", "none")
    if level not in ASR_NOISE_BLOCKS:
        return base
    return base + ASR_NOISE_BLOCKS[level]


def http_post_json(url: str, body: dict, token: str, timeout: int = 30) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-access-token": token,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_json(url: str, token: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"x-access-token": token})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_role_block(role_cfg: dict, system_prompt: str, greeting: str = "") -> dict:
    """Assemble a request body role block (assistant / user / end_checker).

    Pulls provider/model/base_url/api_key/request from config role; injects
    network or proxy depending on base_url; applies model quirks to request.
    """
    request = dict(role_cfg.get("request") or {})
    apply_quirks_to_request(request, role_cfg.get("model", ""))

    block = {
        "provider": role_cfg.get("provider", "openai"),
        "model": role_cfg["model"],
        "llm_base_url": role_cfg["llm_base_url"],
        "llm_api_key": role_cfg["llm_api_key"],
        "request": request,
        "system_prompt": system_prompt,
    }
    # network OR proxy (mutually exclusive at request body level)
    block.update(role_network_block(role_cfg["llm_base_url"]))
    if greeting:
        block["greeting"] = greeting
    return block


def build_chat_body(config: dict, agent_prompt: str, persona: dict, end_description: str) -> dict:
    runtime = config.get("runtime", {})
    return {
        "runtime": {
            "max_turns": runtime.get("max_turns", 20),
            "start_agent": runtime.get("start_agent", "assistant"),
            "min_messages_before_end_check": runtime.get("min_messages_before_end_check", 6),
            "timeout_seconds": runtime.get("timeout_seconds", 180),
        },
        "assistant": build_role_block(
            config["agent_a"],
            system_prompt=agent_prompt,
            greeting=config.get("greeting", ""),
        ),
        "user": build_role_block(
            config["agent_b"],
            system_prompt=build_user_system_prompt(persona),
            greeting=config.get("greeting", ""),
        ),
        "end_checker": {
            **build_role_block(
                config["end_checker"],
                system_prompt="你负责判断这通电话是否应该结束。只能返回严格 JSON。",
            ),
            "end_description": end_description,
        },
        "verbose": False,
    }


def build_simulation_body(config: dict, agent_prompt: str, persona: dict, end_description: str, count: int) -> dict:
    body = build_chat_body(config, agent_prompt, persona, end_description)
    body["count"] = count
    return body


def to_transcript(history: list[dict]) -> list[dict]:
    speaker_map = {"assistant": "agent", "user": "user"}
    return [
        {"speaker": speaker_map[m["role"]], "text": m.get("content", "")}
        for m in (history or [])
        if m.get("role") in speaker_map
    ]


def max_latency_ms(history: list[dict]) -> int:
    out = 0
    for m in history or []:
        m_metrics = m.get("metrics") or {}
        lat = m_metrics.get("latency_ms") or 0
        if lat > out:
            out = lat
    return out


def preflight_check_models(config: dict) -> list[str]:
    """Return list of error strings if any role's model is unsupported (e.g. gpt-5*-pro)."""
    errs = []
    for role in ("agent_a", "agent_b", "end_checker"):
        m = (config.get(role) or {}).get("model") or ""
        if is_pro_model_blocked(m):
            errs.append(f"role={role} model={m!r} can't go through chat completions endpoint; switch to *-chat-latest or a non-pro variant")
    return errs


def load_default_end_description(workspace: Path) -> str:
    """Try to load end_description from workspace/prompts/<id>/criteria.json, else use a generic template."""
    prompts_dir = workspace / "prompts"
    if prompts_dir.exists():
        for proj in prompts_dir.iterdir():
            criteria_path = proj / "criteria.json"
            if criteria_path.exists():
                try:
                    c = json.loads(criteria_path.read_text())
                    if c.get("end_description"):
                        return c["end_description"]
                except Exception:
                    pass
    return (
        "满足以下任一条件时，结束通话：1) 客户明确没有意向（拒绝/已经处理/范围外需求）；"
        "2) 任意一方说再见或明显结束话术。其余情况继续通话。"
        "返回严格 JSON：{\"should_end\": true/false, \"reason\": \"...\"}。"
    )


def find_round_dir(workspace: Path, round_name: str | None) -> Path:
    """Find <workspace>/prompts/<id>/iterations/<round_name>/. If round_name omitted, pick the largest round-NN."""
    prompts_dir = workspace / "prompts"
    if not prompts_dir.exists():
        raise SystemExit(f"missing {prompts_dir}")
    projects = [p for p in prompts_dir.iterdir() if p.is_dir()]
    if not projects:
        raise SystemExit(f"no project subdir under {prompts_dir}")
    if len(projects) > 1:
        raise SystemExit(f"multiple projects under {prompts_dir}, specify which via --project")
    project = projects[0]
    iters = project / "iterations"
    if round_name:
        rd = iters / round_name
        if not rd.exists():
            raise SystemExit(f"missing {rd}")
        return rd
    # auto-pick latest
    rounds = sorted([d for d in iters.iterdir() if d.is_dir() and d.name.startswith("round-")])
    if not rounds:
        raise SystemExit(f"no round-* under {iters}; create round-01/prompt.md first")
    return rounds[-1]


# -------- smoke mode --------

def run_smoke(workspace: Path, config: dict) -> int:
    """Smoke: POST /chat with first persona × max_turns=4. Print per-turn latency vs threshold."""
    round_dir = find_round_dir(workspace, None)
    pool_path = round_dir.parent.parent / "personas" / "pool.jsonl"
    pool = load_jsonl(pool_path)
    if not pool:
        print(f"empty persona pool at {pool_path}", file=sys.stderr)
        return 2
    persona = pool[0]

    smoke_dir = round_dir / "smoke"
    smoke_dir.mkdir(parents=True, exist_ok=True)

    agent_prompt = (round_dir / "prompt.md").read_text()
    end_description = load_default_end_description(workspace)

    # override runtime for smoke
    body = build_chat_body(config, agent_prompt, persona, end_description)
    body["runtime"]["max_turns"] = 4
    body["runtime"]["min_messages_before_end_check"] = 8  # don't end early

    dispatcher = config["remote_server"].rstrip("/")
    token = config["access_token"]
    worker_timeout = config.get("worker_timeout_seconds", 120)
    poll_max = config.get("concurrency", {}).get("poll_max_total_sec", 600)

    print(f"smoke: POST {dispatcher}/chat (persona={persona.get('id')}, max_turns=4)", flush=True)
    try:
        resp = http_post_json(f"{dispatcher}/chat", body, token, timeout=30)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"submit failed HTTP {e.code}: {body_text}", file=sys.stderr)
        return 3
    chat_id = resp.get("chat_id")
    if not chat_id:
        print(f"no chat_id in response: {resp}", file=sys.stderr)
        return 3
    print(f"chat_id={chat_id}, polling...", flush=True)

    started = time.time()
    while True:
        time.sleep(3)
        if time.time() - started > poll_max:
            print(f"client-side timeout after {poll_max}s", file=sys.stderr)
            return 4
        try:
            poll = http_get_json(f"{dispatcher}/chat/{chat_id}", token)
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr)
            continue
        st = poll.get("status")
        print(f"  [{int(time.time()-started)}s] status={st}", flush=True)
        if st in ("succeeded", "failed", "timeout"):
            break

    result = poll.get("result") or {}
    history = result.get("history") or []
    transcript = to_transcript(history)

    # save
    record = {
        "conv_id": "C-smoke-001",
        "persona_id": persona.get("id"),
        "asr_noise": persona.get("asr_noise", "none"),
        "completed_at": iso_now(),
        "n_turns": result.get("turns_used") or len(transcript),
        "status": st,
        "transcript": transcript,
        "stop_reason": result.get("stop_reason"),
        "usage": result.get("usage"),
    }
    append_jsonl(smoke_dir / "transcripts.jsonl", record)

    # report
    print(f"\n=== smoke result ===", flush=True)
    print(f"status: {st}")
    print(f"turns: {record['n_turns']}")
    print(f"stop_reason: {record['stop_reason']}")
    if st != "succeeded":
        print(f"⚠️ smoke did NOT succeed. raw response:")
        print(json.dumps(poll, ensure_ascii=False, indent=2)[:2000])
        return 5

    # latency check
    max_lat = max_latency_ms(history)
    threshold_ms = int(worker_timeout * 1000 * 0.7)
    print(f"\nmax per-turn latency: {max_lat} ms")
    print(f"worker_timeout × 0.7 threshold: {threshold_ms} ms")
    if max_lat > threshold_ms:
        print(f"⚠️  WARNING: latency {max_lat} ms exceeds 70% of worker_timeout. Main run likely to timeout.")
        print(f"   → Reduce request.max_tokens, switch to a non-reasoning model, or ask dispatcher maintainer to raise worker timeout.")
    else:
        print(f"✓ latency within safe range. ok to proceed to main run.")

    print(f"\ntranscript head (first 6 turns):")
    for t in transcript[:6]:
        print(f"  {t['speaker']}: {t['text'][:80]}")

    return 0


# -------- main mode (per-persona /simulation) --------

def run_main(workspace: Path, config: dict, round_name: str) -> int:
    round_dir = find_round_dir(workspace, round_name)
    pool_path = round_dir.parent.parent / "personas" / "pool.jsonl"
    pool = load_jsonl(pool_path)
    if not pool:
        print(f"empty pool at {pool_path}", file=sys.stderr)
        return 2

    agent_prompt = (round_dir / "prompt.md").read_text()
    end_description = load_default_end_description(workspace)

    K = config.get("repeats_per_persona", 2)
    dispatcher = config["remote_server"].rstrip("/")
    token = config["access_token"]
    worker_timeout = config.get("worker_timeout_seconds", 120)
    conc = config.get("concurrency", {})
    SUBMIT_INTERVAL = conc.get("submit_interval_sec", 1.0)
    POLL_INTERVAL = conc.get("poll_interval_sec", 5.0)
    POLL_MAX = conc.get("poll_max_total_sec", 1800)
    MAX_IN_FLIGHT = conc.get("max_in_flight", 10)

    transcripts_path = round_dir / "transcripts.jsonl"
    failed_path = round_dir / "failed.jsonl"
    progress_path = round_dir / "progress.log"

    # resume: count completed per persona
    done_counts: dict[str, int] = {}
    for r in load_jsonl(transcripts_path):
        done_counts[r["persona_id"]] = done_counts.get(r["persona_id"], 0) + 1
    todo = [p for p in pool if done_counts.get(p["id"], 0) < K]
    print(f"resume: {sum(done_counts.values())} done across {len(done_counts)} personas, {len(todo)} personas todo (K={K})", flush=True)
    if not todo:
        print("nothing to do.", flush=True)
        return 0

    in_flight: list[dict] = []
    todo_iter = iter(todo)
    started_at = time.time()
    last_submit = 0.0
    last_progress = started_at
    success_chats = sum(done_counts.values())
    failed_chats = 0

    def progress_line():
        elapsed = time.time() - started_at
        completed = success_chats + failed_chats
        target_total = len(pool) * K
        line = (
            f"[{iso_now()}] target={target_total} done={completed} "
            f"(success={success_chats}, failed={failed_chats}) "
            f"in_flight={len(in_flight)}"
        )
        print(line, flush=True)
        with open(progress_path, "a") as f:
            f.write(line + "\n")

    while True:
        # submit
        if time.time() - last_submit >= SUBMIT_INTERVAL and len(in_flight) < MAX_IN_FLIGHT:
            try:
                p = next(todo_iter)
                remaining = K - done_counts.get(p["id"], 0)
                if remaining <= 0:
                    continue
                body = build_simulation_body(config, agent_prompt, p, end_description, count=remaining)
                try:
                    resp = http_post_json(f"{dispatcher}/simulation", body, token, timeout=30)
                    sim_id = resp.get("chat_id") or resp.get("simulation_id")
                    if not sim_id:
                        raise RuntimeError(f"no id in response: {resp}")
                    in_flight.append({
                        "persona": p,
                        "count": remaining,
                        "sim_id": sim_id,
                        "submitted_at": time.time(),
                        "last_polled_at": 0.0,
                    })
                    last_submit = time.time()
                except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
                    body_text = ""
                    if isinstance(e, urllib.error.HTTPError):
                        try:
                            body_text = e.read().decode("utf-8", errors="replace")[:300]
                        except Exception:
                            pass
                    append_jsonl(failed_path, {
                        "persona_id": p["id"],
                        "failed_at": iso_now(),
                        "error_type": "submit_http_error",
                        "error_msg": f"{e}; body={body_text}",
                    })
                    failed_chats += remaining
            except StopIteration:
                pass

        # poll in-flight
        for state in list(in_flight):
            now = time.time()
            if now - state["last_polled_at"] < POLL_INTERVAL:
                continue
            state["last_polled_at"] = now
            sim_url = f"{dispatcher}/simulation/{state['sim_id']}"
            # fallback: some dispatchers expose results via /chat/<id>
            try:
                poll = http_get_json(sim_url, token)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    poll = http_get_json(f"{dispatcher}/chat/{state['sim_id']}", token)
                else:
                    if now - state["submitted_at"] >= POLL_MAX:
                        append_jsonl(failed_path, {
                            "persona_id": state["persona"]["id"],
                            "failed_at": iso_now(),
                            "error_type": "poll_http_error",
                            "error_msg": str(e),
                        })
                        failed_chats += state["count"]
                        in_flight.remove(state)
                    continue
            except Exception as e:
                if now - state["submitted_at"] >= POLL_MAX:
                    append_jsonl(failed_path, {
                        "persona_id": state["persona"]["id"],
                        "failed_at": iso_now(),
                        "error_type": "unknown",
                        "error_msg": str(e),
                    })
                    failed_chats += state["count"]
                    in_flight.remove(state)
                continue

            st = poll.get("status")
            if st == "succeeded":
                # parse: may have result.chats[] (sim shape) or result.history (chat shape)
                result = poll.get("result") or {}
                chats = result.get("chats")
                if not chats:
                    # fallback: single-chat shape, wrap
                    if result.get("history"):
                        chats = [result]
                    else:
                        chats = []
                p = state["persona"]
                for i, c in enumerate(chats):
                    hist = c.get("history") or []
                    transcript = to_transcript(hist)
                    record = {
                        "conv_id": f"{p['id']}_t{i+1:02d}",
                        "persona_id": p["id"],
                        "persona_name": p.get("name", ""),
                        "asr_noise": p.get("asr_noise", "none"),
                        "completed_at": iso_now(),
                        "n_turns": c.get("turns_used") or len(transcript),
                        "status": "completed",
                        "transcript": transcript,
                        "stop_reason": c.get("stop_reason"),
                        "usage": c.get("usage"),
                    }
                    append_jsonl(transcripts_path, record)
                    success_chats += 1
                    # warn on latency
                    max_lat = max_latency_ms(hist)
                    if max_lat > worker_timeout * 1000 * 0.7:
                        print(f"⚠️ persona={p['id']} turn latency={max_lat}ms (worker_timeout×0.7 = {int(worker_timeout*1000*0.7)}ms)", flush=True)
                in_flight.remove(state)
            elif st in ("failed", "timeout"):
                append_jsonl(failed_path, {
                    "persona_id": state["persona"]["id"],
                    "failed_at": iso_now(),
                    "error_type": st,
                    "error_msg": poll.get("error") or (poll.get("result") or {}).get("stop_reason") or "",
                    "sim_id": state["sim_id"],
                })
                failed_chats += state["count"]
                in_flight.remove(state)
            elif now - state["submitted_at"] >= POLL_MAX:
                append_jsonl(failed_path, {
                    "persona_id": state["persona"]["id"],
                    "failed_at": iso_now(),
                    "error_type": "client_timeout",
                    "error_msg": f"poll_max {POLL_MAX}s reached",
                    "sim_id": state["sim_id"],
                })
                failed_chats += state["count"]
                in_flight.remove(state)

        if time.time() - last_progress >= 60:
            progress_line()
            last_progress = time.time()

        # done?
        if not in_flight:
            try:
                # peek next - if StopIteration, we're done after current in_flight empty
                pass
            except Exception:
                pass
            # one final break check
            remaining_persona = len(pool) - len([p for p in pool if done_counts.get(p["id"], 0) >= K])
            if success_chats + failed_chats >= len(pool) * K:
                break
            # otherwise iterator might still have items to submit; small wait then continue
            try:
                # peek by re-deriving
                pending = [p for p in pool if done_counts.get(p["id"], 0) + sum(1 for r in load_jsonl(transcripts_path) if r["persona_id"] == p["id"]) < K]
                if not pending and not in_flight:
                    break
            except Exception:
                pass

        time.sleep(0.3)

    progress_line()
    print(f"\ndone. success_chats={success_chats} failed_chats={failed_chats}", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", type=Path)
    ap.add_argument("--smoke", action="store_true", help="Phase D: POST /chat once for sanity check")
    ap.add_argument("--round", dest="round_name", help="round name like 'round-01' (default: latest)")
    args = ap.parse_args()
    if not args.workspace.exists():
        print(f"workspace {args.workspace} not found", file=sys.stderr)
        sys.exit(1)
    config = json.loads((args.workspace / "config.json").read_text())

    # preflight: check models
    errs = preflight_check_models(config)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    if args.smoke:
        sys.exit(run_smoke(args.workspace, config))
    else:
        sys.exit(run_main(args.workspace, config, args.round_name))


if __name__ == "__main__":
    main()
