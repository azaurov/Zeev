"""Quantum reasoning module for Zeev.

Pipeline:
  1. LLM maps the user's idea to a quantum circuit spec (options, phases, entangled pairs)
  2. Simulate the circuit (Qiskit via subprocess if available, pure-Python fallback)
  3. LLM interprets the interference pattern as insight
"""
import cmath
import json
import math
import subprocess
from pathlib import Path

QISKIT_PYTHON = str(Path.home() / "qiskit-env" / "bin" / "python3")
RUNNER_SCRIPT = Path(__file__).parent / "quantum_runner.py"


# ---------------------------------------------------------------------------
# Pure-Python statevector simulator (fallback when Qiskit is unavailable)
# ---------------------------------------------------------------------------

def _simulate_pure_python(spec):
    options = spec["options"]
    phases = spec["phases"]
    entangled_pairs = spec.get("entangled_pairs", [])
    n = len(options)
    N = 1 << n

    state = [0.0 + 0j] * N
    state[0] = 1.0 + 0j

    def apply_h(st, qubit):
        ns = list(st)
        for i in range(N):
            if not (i >> qubit) & 1:
                j = i | (1 << qubit)
                a, b = st[i], st[j]
                ns[i] = (a + b) / math.sqrt(2)
                ns[j] = (a - b) / math.sqrt(2)
        return ns

    def apply_rz(st, qubit, theta):
        ns = list(st)
        for i in range(N):
            bit = (i >> qubit) & 1
            ns[i] = st[i] * (cmath.exp(-1j * theta / 2) if bit == 0 else cmath.exp(1j * theta / 2))
        return ns

    def apply_cx(st, ctrl, tgt):
        ns = list(st)
        for i in range(N):
            if (i >> ctrl) & 1 and not (i >> tgt) & 1:
                j = i | (1 << tgt)
                ns[i], ns[j] = st[j], st[i]
        return ns

    for i in range(n):
        state = apply_h(state, i)
    for i, phase in enumerate(phases):
        state = apply_rz(state, i, float(phase))
    for a, b in entangled_pairs:
        state = apply_cx(state, int(a), int(b))
    for i in range(n):
        state = apply_h(state, i)

    raw = {format(i, f"0{n}b"): abs(state[i]) ** 2 for i in range(N)}
    total = sum(raw.values())
    probs = {k: v / total for k, v in sorted(raw.items(), key=lambda x: -x[1]) if v / total > 0.001}
    counts = {k: max(1, int(v * 1024)) for k, v in probs.items()}
    return {"probabilities": probs, "counts": counts, "total_shots": 1024}


# ---------------------------------------------------------------------------
# Qiskit runner (subprocess into the ~/qiskit-env venv)
# ---------------------------------------------------------------------------

def _run_qiskit(spec):
    if not Path(QISKIT_PYTHON).exists() or not RUNNER_SCRIPT.exists():
        return None, "qiskit_unavailable"
    try:
        proc = subprocess.run(
            [QISKIT_PYTHON, str(RUNNER_SCRIPT)],
            input=json.dumps(spec).encode(),
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return None, proc.stderr.decode()
        return json.loads(proc.stdout.decode()), None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _llm_circuit_spec(idea, llm_fn):
    prompt = (
        f'The user shared this idea or dilemma: "{idea}"\n\n'
        "Map it to a quantum circuit. Identify 2-4 core options, dimensions, or tensions.\n"
        "For each, assign a phase angle (0.0 to 6.28 radians) reflecting inner alignment:\n"
        "  - Low angle (near 0): clear, aligned, harmonious with the user's core self\n"
        "  - High angle (near π=3.14): uncertain, conflicted, pulls against the grain\n"
        "Identify which pairs of options are most interdependent (entangled).\n\n"
        "Output JSON only — no commentary, no markdown:\n"
        '{"options": ["option A", "option B"], '
        '"phases": [0.8, 2.3], '
        '"entangled_pairs": [[0, 1]], '
        '"reflection": "one sentence on what the core tension is"}'
    )
    msgs = [
        {"role": "system", "content": "You are a creative idea mapper. Output only valid JSON."},
        {"role": "user", "content": prompt},
    ]
    text, err = llm_fn(msgs, max_tokens=400, json_mode=True)
    if err or not text:
        return None, err or "no response"
    try:
        spec = json.loads(text)
        assert isinstance(spec.get("options"), list) and 2 <= len(spec["options"]) <= 4
        assert isinstance(spec.get("phases"), list) and len(spec["phases"]) == len(spec["options"])
        return spec, None
    except Exception as e:
        return None, f"invalid spec ({e}): {text[:200]}"


def _llm_interpret(idea, spec, result, llm_fn, past_insights=None):
    options = spec["options"]
    phases = spec["phases"]
    probs = result["probabilities"]
    reflection = spec.get("reflection", "")
    n = len(options)

    outcome_lines = []
    for bits, prob in list(probs.items())[:6]:
        # Qiskit bit strings: rightmost character = qubit 0
        active = [options[i] for i in range(n) if i < len(bits) and bits[-(i + 1)] == "1"]
        label = " + ".join(active) if active else "none / reset"
        outcome_lines.append(f"  {label}: {prob * 100:.1f}%")

    phase_lines = [f"  {opt}: {ph:.2f} rad ({'aligned' if ph < 1.6 else 'conflicted' if ph > 2.5 else 'neutral'})"
                   for opt, ph in zip(options, phases)]

    past_block = ""
    if past_insights:
        lines = "\n".join(f"  - [{p['idea']}]: {p['interpretation'][:120]}…" for p in past_insights)
        past_block = f"\n\nRelated patterns from prior quantum sessions:\n{lines}\n"

    prompt = (
        f'Quantum circuit explored: "{idea}"\n\n'
        f"Reflection: {reflection}\n\n"
        "Options encoded as qubits:\n"
        + "\n".join(f"  q{i}: {opt}" for i, opt in enumerate(options))
        + "\n\nPhase alignment (low=clear, high=conflicted):\n"
        + "\n".join(phase_lines)
        + "\n\nAfter quantum interference, top measurement outcomes:\n"
        + "\n".join(outcome_lines)
        + past_block
        + "\n\nInterpret this as Zeev, a thoughtful companion. What does the interference pattern reveal "
        "about this dilemma? Speak in 3-4 sentences directly to Alex. Be specific, not vague."
    )
    msgs = [
        {"role": "system", "content": "You are Zeev, a humble, calm, insightful AI companion."},
        {"role": "user", "content": prompt},
    ]
    # llm_fn is expected to pass reasoning_effort="none"/"low" for the
    # model it routes to (see quantum_daily.py's _llm_fn and zeev.py's
    # _quantum_llm) -- without that, qwen3.6-27b inlines its <think>
    # reasoning into content and can burn the whole budget without ever
    # reaching the answer (finish_reason "length", empty after stripping).
    # Found live 2026-08-25 chasing a 12-day gap in quantum_insights.
    text, err = llm_fn(msgs, max_tokens=400)
    return text or "", err


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def quantum_reason(idea, llm_fn, past_insights=None):
    """Run the full quantum reasoning pipeline.

    Returns (interpretation, spec, result, error).
    On error, interpretation is None and error is a string.
    past_insights: list of dicts with 'idea' and 'interpretation' keys,
                   injected as context to improve the interpretation.
    """
    spec, err = _llm_circuit_spec(idea, llm_fn)
    if err:
        return None, None, None, f"circuit mapping: {err}"

    result, err = _run_qiskit(spec)
    if err:
        result = _simulate_pure_python(spec)

    interpretation, err = _llm_interpret(idea, spec, result, llm_fn, past_insights=past_insights)
    if err:
        return None, spec, result, f"interpretation: {err}"

    return interpretation, spec, result, None
