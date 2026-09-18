# Fast gate before pushing or deploying. `make check` is what the pre-push hook
# and CI both run, so there is one definition of "did I break something".
#
# Deliberately NOT a replacement for ./deploy.sh -- that remains the only
# sanctioned path to the Pi and runs this same suite plus the HEAD assertion,
# the migration and the startup-banner health poll.

PYTEST ?= python3 -m pytest
# -n auto is a hard usage error, not a graceful no-op, when pytest-xdist is
# missing -- so probe for it rather than assuming.
XDIST := $(shell python3 -c "import xdist" 2>/dev/null && echo "-n auto")

.PHONY: check test smoke regressions help

help:
	@echo "make check       - tests + smoke (what the pre-push hook runs)"
	@echo "make test        - full pytest suite"
	@echo "make regressions - only the shipped-bug regression tests (fast)"
	@echo "make smoke       - host-dependent shell smoke tests"

check: test smoke

# External `timeout`, not pytest-timeout: that plugin is not installed here and
# `--timeout=` is a hard usage error without it. deploy.sh bounds the suite the
# same way, so the two agree.
TEST_TIMEOUT ?= 600
test:
	timeout $(TEST_TIMEOUT) $(PYTEST) $(XDIST) -q --no-header tests/

regressions:
	$(PYTEST) -q --no-header tests/test_regression_history.py tests/test_wake_capture.py

# Each smoke script exits 0 pass / 1 fail / 77 skip. A skip is NOT a pass: it
# means the host does not own that thing, and it is reported as such rather
# than folded into success.
smoke:
	@rc=0; \
	for s in scripts/smoke/*.sh; do \
		out=$$(bash $$s 2>&1); code=$$?; \
		case $$code in \
			0)  printf '  PASS %s\n' "$$(basename $$s)";; \
			77) printf '  SKIP %s (%s)\n' "$$(basename $$s)" "$$(echo "$$out" | head -1)";; \
			*)  printf '  FAIL %s\n%s\n' "$$(basename $$s)" "$$out"; rc=1;; \
		esac; \
	done; \
	exit $$rc
