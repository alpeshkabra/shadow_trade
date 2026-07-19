.PHONY: install demo test api

install:
	pip install -r requirements.txt

demo:
	python run_demo.py

test:
	pytest -q

api:
	PYTHONPATH=src uvicorn shadow_trade.asgi:app --reload
