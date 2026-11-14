.PHONY: test slo gate up demo

test:
	python -m pytest -q

slo:
	python golden-path/slo/generate.py

gate:
	python scripts/policy_gate.py

up:
	docker compose up --build

demo:
	python scripts/demo_fault.py
