.PHONY: build pi clean test

# Local build (amd64, for dev/test on the dev box)
build:
	go build -o bin/zeev-audio ./cmd/zeev-audio

# Cross-compile for Pi Zero 2W.
# NOTE: GOARCH is intentionally left as a variable.
#   Pi Zero 2W is Cortex-A53 (ARMv8).
#   - 64-bit Raspberry Pi OS: GOARCH=arm64 (no GOARM needed)
#   - 32-bit Raspberry Pi OS: GOARCH=arm GOARM=7
#   Verify with `uname -m` on the Pi before deploying.
#   Current setting assumes 32-bit OS (GOARM=7). Adjust if 64-bit.
GOARCH   ?= arm
GOARM    ?= 7
PI_HOST  ?= ragnar@ragnarok

pi:
	GOOS=linux GOARCH=$(GOARCH) GOARM=$(GOARM) \
		go build -o bin/zeev-audio-pi ./cmd/zeev-audio
	@echo "Binary: bin/zeev-audio-pi"
	@echo "Deploy: scp bin/zeev-audio-pi $(PI_HOST):~/zeev-audio/zeev-audio"

deploy: pi
	ssh $(PI_HOST) mkdir -p ~/zeev-audio
	scp bin/zeev-audio-pi $(PI_HOST):~/zeev-audio/zeev-audio
	scp ../zeev-audio.service $(PI_HOST):~/
	ssh $(PI_HOST) "sudo mv ~/zeev-audio.service /etc/systemd/system/ && \
		sudo systemctl daemon-reload && \
		sudo systemctl enable zeev-audio && \
		sudo systemctl restart zeev-audio && \
		sudo systemctl status zeev-audio"

test:
	go test ./...

clean:
	rm -rf bin/
