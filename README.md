# Workload Initial Screen: Diagnostic Rendering Experiment

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

# 2. Validate all 8 discovery fixtures (baseline failure, reference repair, isolation, groupability)
python -m experiment.validate_fixtures --show-diagnostics

# 3. Execute mock discovery screen (16 episodes, offline)
python -m experiment.schedule --seed 20260911 --run runs/mock_screen
python -m experiment.runner --phase discovery --mock --run runs/mock_screen

# 4. Generate summary report offline
python -m experiment.summarize --run runs/mock_screen
```

## Researcher Workflow (Live Execution)

Invoked only after offline implementation passes:

```bash
# 1. Calibration Phase (up to 8 episodes, 32 generation slots)
python -m experiment.runner --phase calibration --execute --run runs/screen_001

# 2. Save fixture validation results and operational freeze
python -m experiment.validate_fixtures --output runs/screen_001/fixture_validation.json
# (Record calibration review in runs/screen_001/calibration/review.json)
python -m experiment.runner --freeze --run runs/screen_001

# 3. Generate deterministic Latin-square schedule
python -m experiment.schedule --seed 20260911 --run runs/screen_001

# 4. Local preflight validation and budget checks
python -m experiment.runner --phase discovery --preflight --run runs/screen_001

# 5. Live Discovery Phase execution with safe resume
python -m experiment.runner --phase discovery --execute --resume --run runs/screen_001

# 6. Generate final summary
python -m experiment.summarize --run runs/screen_001
```
