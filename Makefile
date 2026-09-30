.DEFAULT_GOAL := help
PYTHON ?= python

.PHONY: help install test coverage run crawl docker-up docker-down lint clean

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## 安装依赖
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

test: ## 运行测试
	$(PYTHON) -m pytest

coverage: ## 运行测试并生成覆盖率报告
	$(PYTHON) -m pytest --cov=. --cov-report=term-missing

run: ## 启动 Web 服务 (http://127.0.0.1:5000)
	$(PYTHON) app.py

crawl: ## 命令行采集一次数据（需先 playwright install chromium）
	$(PYTHON) -c "from data.python_job_scraper import scrape_jobs; scrape_jobs('python', ['北京', '上海', '广州', '深圳'], pages_per_city=3)"

docker-up: ## 启动容器
	docker compose up -d

docker-down: ## 停止容器
	docker compose down

clean: ## 清理缓存与中间产物
	rm -rf .pytest_cache .mypy_cache htmlcov .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
