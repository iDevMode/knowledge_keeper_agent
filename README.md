# KnowledgeKeeper

Three-stage AI agent system for institutional knowledge capture during employee departures. Built by Nukode.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Add your ANTHROPIC_API_KEY to .env
```

## CLI Testing

```bash
# Run Stage 1 Business Interview
python -m agents.stage1_business_interview --mode cli
```

## Running Tests

```bash
pytest tests/ -v
```
