"""
Piloto Fase 0: Validar que un SLM (Qwen3.5:9B via Ollama) puede implementar
un contrato derivado de un requerimiento, y que el codigo pasa los tests.

Uso:
    python scripts/smoke_slm_pilot.py
"""

import json
import re
import sys
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen3.5:9b-8k"

# ---------------------------------------------------------------------------
# 1. EL REQUERIMIENTO (como vendria del SRS)
# ---------------------------------------------------------------------------
#
# REQ-001 (MoSCoW: Must, Tipo: Functional)
#
# "El sistema debe calcular el limite de credito de un cliente basandose en
# su score crediticio, ingresos anuales, deudas existentes e historial de
# pago."

# ---------------------------------------------------------------------------
# 2. EL CONTRATO (lo que recibira el SLM)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are a Python code generator. "
    "Output ONLY valid Python code. "
    "No markdown fences, no explanations, no comments."
)

USER_PROMPT = """\
Complete the body of this Python function. Output ONLY the function body \
(the indented code that goes inside the function). Do not repeat the def line.

# Component: CreditLimitCalculator
# Type: Leaf component (domain logic, pure function)
#
# Rules:
#   1. Base limit = annual_income * 0.15
#   2. Credit score adjustment:
#      - score >= 750: multiply base by 1.5
#      - score >= 650 and < 750: multiply base by 1.2
#      - score >= 600 and < 650: multiply base by 1.0
#      - score < 600: multiply base by 0.5
#   3. Payment history adjustment (applied AFTER score adjustment):
#      - "excellent": multiply current by 1.1
#      - "good": multiply current by 1.0
#      - "fair": multiply current by 0.9
#      - "poor": multiply current by 0.7
#   4. Subtract existing_debt from the adjusted limit
#   5. Clamp the result: minimum 0, maximum 50000
#   6. Round to 2 decimal places
#
# Test cases (input -> expected output):
#   calculate_credit_limit(700, 40000, 0, "good") -> 7200.0
#   calculate_credit_limit(590, 20000, 3000, "poor") -> 0.0

def calculate_credit_limit(credit_score, annual_income, existing_debt, payment_history):
"""


# ---------------------------------------------------------------------------
# 3. LOS TESTS (verificacion automatica)
# ---------------------------------------------------------------------------
# Valores derivados manualmente de las reglas del contrato.

TEST_CASES = [
    # (credit_score, annual_income, existing_debt, payment_history, expected)
    # 1. score>=750, excellent, moderate debt
    #    50000*0.15=7500 *1.5=11250 *1.1=12375 -5000 = 7375
    (780, 50000, 5000, "excellent", 7375.0),
    # 2. score<600, fair, high debt -> clamp to 0
    #    30000*0.15=4500 *0.5=2250 *0.9=2025 -8000 = -5975 -> 0
    (550, 30000, 8000, "fair", 0.0),
    # 3. score 650-749, good, no debt
    #    40000*0.15=6000 *1.2=7200 *1.0=7200 -0 = 7200
    (700, 40000, 0, "good", 7200.0),
    # 4. score<600, poor, debt > adjusted -> clamp to 0
    #    20000*0.15=3000 *0.5=1500 *0.7=1050 -3000 = -1950 -> 0
    (590, 20000, 3000, "poor", 0.0),
    # 5. score>=750, excellent, high income -> clamp to max
    #    100000*0.15=15000 *1.5=22500 *1.1=24750 -20000 = 4750
    (800, 100000, 20000, "excellent", 4750.0),
    # 6. Casos no en el contrato (verificacion de generalizacion)
    #    60000*0.15=9000 *1.2=10800 *1.0=10800 -10000 = 800
    (650, 60000, 10000, "good", 800.0),
    #    50000*0.15=7500 *1.0=7500 *0.9=6750 -1000 = 5750
    (600, 50000, 1000, "fair", 5750.0),
    #    10000*0.15=1500 *0.5=750 *0.7=525 -0 = 525
    (300, 10000, 0, "poor", 525.0),
]


def call_slm() -> str:
    """Send the contract to the SLM and return the generated code."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1, "num_predict": 2048},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())
    return data["message"]["content"]


def build_function(slm_output: str) -> str:
    """Assemble a complete, executable Python function from the SLM output."""
    code = re.sub(r"```python\s*\n?", "", slm_output)
    code = re.sub(r"```\s*$", "", code)
    code = code.strip()

    if code.startswith("def calculate_credit_limit"):
        return code

    body_lines = code.split("\n")
    indented = []
    for line in body_lines:
        if line.strip():
            indented.append("    " + line)
        else:
            indented.append("")
    return (
        "def calculate_credit_limit(credit_score, annual_income, existing_debt, payment_history):\n"
        + "\n".join(indented)
    )


def run_tests(func_code: str) -> tuple[int, int]:
    """Execute the generated function and run test cases."""
    namespace = {}
    try:
        exec(func_code, namespace)
    except SyntaxError as e:
        print(f"  [SYNTAX ERROR] {e}")
        return 0, len(TEST_CASES)

    func = namespace.get("calculate_credit_limit")
    if func is None:
        print("  [ERROR] Function 'calculate_credit_limit' not found")
        return 0, len(TEST_CASES)

    passed = 0
    for score, income, debt, history, expected in TEST_CASES:
        try:
            result = func(score, income, debt, history)
            result = round(result, 2)
            ok = abs(result - expected) < 0.01
        except Exception as e:
            result = f"EXCEPTION: {e}"
            ok = False

        symbol = "✅" if ok else "❌"
        status = "PASS" if ok else "FAIL"
        print(f"  {symbol} [{status}] ({score}, {income}, {debt}, \"{history}\")")
        print(f"           esperado: {expected} | obtenido: {result}")
        if ok:
            passed += 1

    return passed, len(TEST_CASES)


def main():
    print("=" * 70)
    print("PILOTO FASE 0: SLM implementando un contrato")
    print(f"Modelo: {MODEL}")
    print("=" * 70)

    print("\n[1/3] Enviando contrato al SLM...")
    raw = call_slm()
    print(f"      Respuesta: {len(raw)} chars")

    print("\n[2/3] Ensamblando funcion Python...")
    func_code = build_function(raw)

    print("\n--- Codigo generado ---")
    for line in func_code.split("\n"):
        print(f"  {line}")
    print("--- Fin ---\n")

    print("[3/3] Ejecutando tests...")
    passed, total = run_tests(func_code)

    print("\n" + "=" * 70)
    pct = (passed / total) * 100 if total > 0 else 0
    if passed == total:
        print(f"RESULTADO: ✅ {passed}/{total} tests pasaron ({pct:.0f}%)")
        print("El SLM implemento el contrato correctamente.")
    elif passed >= total * 0.8:
        print(f"RESULTADO: ⚠️  {passed}/{total} tests pasaron ({pct:.0f}%)")
        print("Cerca del objetivo. Los fallos indican donde afinar.")
    else:
        print(f"RESULTADO: ❌ {passed}/{total} tests pasaron ({pct:.0f}%)")
        print("El SLM no logro implementar el contrato.")
    print("=" * 70)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
