"""Verification script for ProcedureAgent's fetch_protocol tool.
1. Directly calls the fetch_protocol wrapper and writes its JSON output to a file.
2. Instantiates the _ProcedureAgentDirect class, runs the _fallback method with a sample incident,
   and writes the resulting dictionary to the same file.
"""
import sys, os, json

PROJECT_ROOT = r"C:/Users/epifh/Desktop/Clase/TFG/code"
sys.path.append(PROJECT_ROOT)

OUTPUT_PATH = os.path.join(PROJECT_ROOT, "verify_fetch_output.txt")

def write_line(line: str):
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")

from agents.procedure_agent import fetch_protocol
write_line("--- Direct fetch_protocol call ---")
result = fetch_protocol('cardiac_arrest', 'critical')
write_line("Result JSON string: " + result)

from agents.procedure_agent import _ProcedureAgentDirect
from smolagents import InferenceClientModel

model = InferenceClientModel('Qwen/Qwen2.5-72B-Instruct')
agent = _ProcedureAgentDirect(model)
incident = {
    "incident_type": "cardiac_arrest",
    "severity": "critical",
    "extra_context": ""
}
write_line("--- ProcedureAgent fallback ---")
fallback_result = agent._fallback(incident)
write_line("Fallback returned dict: " + json.dumps(fallback_result, ensure_ascii=False))
