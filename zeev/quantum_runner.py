#!/usr/bin/env python3
"""Quantum circuit runner — reads circuit spec JSON from stdin, outputs results JSON to stdout.
Run with the Qiskit virtualenv Python: ~/qiskit-env/bin/python3 quantum_runner.py"""
import json
import math
import sys


def run_circuit(spec):
    from qiskit import QuantumCircuit
    from qiskit_aer import AerSimulator

    options = spec["options"]
    phases = spec["phases"]
    entangled_pairs = spec.get("entangled_pairs", [])
    n = len(options)

    qc = QuantumCircuit(n, n)
    for i in range(n):
        qc.h(i)
    for i, phase in enumerate(phases):
        qc.rz(float(phase), i)
    for a, b in entangled_pairs:
        qc.cx(int(a), int(b))
    for i in range(n):
        qc.h(i)
    qc.measure(list(range(n)), list(range(n)))

    sim = AerSimulator()
    job = sim.run(qc, shots=1024)
    counts = job.result().get_counts()

    total = sum(counts.values())
    probs = {k: v / total for k, v in sorted(counts.items(), key=lambda x: -x[1])}
    return {"counts": counts, "probabilities": probs, "total_shots": total}


if __name__ == "__main__":
    spec = json.loads(sys.stdin.read())
    result = run_circuit(spec)
    print(json.dumps(result))
