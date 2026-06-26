.PHONY: build pi clean test

# Local build (amd64, for dev/test on the dev box)
build:
	go build -o bin/zeev-audio ./cmd/zeev-audio

# Cross-compile for Pi Zero 2W (64-bit Raspberry Pi OS, Cortex-A53/ARMv8).
# uname -m = aarch64 → GOARCH=arm64, no GOARM needed.
GOARCH   ?= arm64
PI_HOST  ?= ragnar@ragnarok

pi:
	GOOS=linux GOARCH=$(GOARCH) \
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
