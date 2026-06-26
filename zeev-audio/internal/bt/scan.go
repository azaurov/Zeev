package bt

import (
	"bufio"
	"bytes"
	"fmt"
	"log"
	"os/exec"
	"regexp"
	"strings"
)

var deviceRe = regexp.MustCompile(`\[NEW\]\s+Device\s+([0-9A-F:]+)\s+(.+)`)

// ScanResult is one discovered Bluetooth device.
type ScanResult struct {
	MAC  string
	Name string
}

// Scan runs bluetoothctl scan for timeoutSec seconds and returns discovered devices.
// Parses all output after completion to avoid readline hangs on ANSI escape sequences.
func Scan(timeoutSec int) ([]ScanResult, error) {
	if timeoutSec <= 0 {
		timeoutSec = 10
	}
	cmd := exec.Command("timeout", fmt.Sprintf("%d", timeoutSec), "bluetoothctl", "scan", "on")
	out, err := cmd.Output()
	// timeout exits with code 124 on expiry — that's the normal path, not an error.
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok && ee.ExitCode() != 124 {
			return nil, fmt.Errorf("bluetoothctl scan: %w", err)
		}
	}
	log.Printf("bt: scan complete, parsing %d bytes of output", len(out))
	return parseDevices(out), nil
}

func parseDevices(data []byte) []ScanResult {
	seen := map[string]bool{}
	var results []ScanResult
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line := cleanANSI(scanner.Text())
		m := deviceRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		mac := strings.ToUpper(strings.TrimSpace(m[1]))
		name := strings.TrimSpace(m[2])
		if seen[mac] {
			continue
		}
		seen[mac] = true
		results = append(results, ScanResult{MAC: mac, Name: name})
	}
	return results
}

var ansiRe = regexp.MustCompile(`\x1b\[[0-9;]*[a-zA-Z]`)

func cleanANSI(s string) string {
	return ansiRe.ReplaceAllString(s, "")
}
