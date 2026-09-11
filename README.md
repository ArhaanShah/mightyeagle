# Screen 003: Diagnostic Rendering Experiment

This repository implements the behavioral science experiment harness comparing the effect of **expanded** vs. **grouped** type-checker diagnostic rendering on LLM code repair.

## Prerequisites

- Python 3.12+
- Virtual environment at `.venv`
- Installed dependencies:
  ```bash
  pip install -r requirements-lock.txt
  ```
- `.env` file containing `GROQ_API_KEY`
- Docker Desktop with the validation image built once:
  ```bash
  docker build -f experiment/Dockerfile -t workload-screen-validator:20260911 experiment
  ```

## Verification & Acceptance Suite (Offline)

All offline commands require no API calls and no credentials:

```bash
# 1. Run full unit test suite
pytest -q tests

# 2. Validate the four final-rerun fixtures
python -m experiment.validate_fixtures --screen final_rerun --show-diagnostics

# 3. Execute mock discovery screen (16 episodes, offline)
python -m experiment.schedule --seed 20260912 --run runs/mock_final --mock
python -m experiment.runner --phase discovery --mock --run runs/mock_final

# 4. Generate summary report offline
python -m experiment.summarize --run runs/mock_final
```

## Researcher Workflow (Live Execution)

Invoked only after offline implementation passes:

```bash
# 1. Calibration Phase (two required episodes, up to 12 generations including replacements)
python -m experiment.runner --phase calibration --execute --run runs/screen_003

# 2. Save fixture validation results and operational freeze
python -m experiment.validate_fixtures --screen final_rerun --output runs/screen_003/fixture_validation.json
# Manually inspect generation-1 arguments and record runs/screen_003/calibration/review.json
python -m experiment.runner --calibration-gate --run runs/screen_003
python -m experiment.runner --freeze --require-clean-git --run runs/screen_003

# 3. Generate deterministic Latin-square schedule
python -m experiment.schedule --seed 20260912 --run runs/screen_003

# 4. Local preflight validation and budget checks
python -m experiment.runner --phase discovery --preflight --run runs/screen_003

# 5. Live Discovery Phase execution with safe resume
python -m experiment.runner --phase discovery --execute --resume --run runs/screen_003

# 6. Complete manual review, summarize, and audit
python -m experiment.review --run runs/screen_003
python -m experiment.summarize --run runs/screen_003
python -m experiment.audit_run --run runs/screen_003
```
