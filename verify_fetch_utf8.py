import sys, os, json

PROJECT_ROOT = r"C:/Users/epifh/Desktop/Clase/TFG/code"
sys.path.append(PROJECT_ROOT)

from agents.procedure_agent import fetch_protocol

result = fetch_protocol('cardiac_arrest', 'critical')

out_path = os.path.join(PROJECT_ROOT, "fetch_result.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(result)

print("WROTE", out_path)
