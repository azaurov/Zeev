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
//
// Uses bluetoothctl's own --timeout flag, not an external `timeout` wrapper:
// on bluez 5.82+, `bluetoothctl scan on` invoked as a one-shot subcommand
// enables discovery and exits almost instantly (~50ms) rather than blocking
// for the scan window, so wrapping it in `timeout N` never had anything to
// kill — it returned immediately with no discovered devices. `--timeout N`
// is bluez's own non-interactive-mode flag and genuinely blocks for N
// seconds, streaming [NEW] Device lines to stdout as they arrive.
func Scan(timeoutSec int) ([]ScanResult, error) {
	if timeoutSec <= 0 {
		timeoutSec = 10
	}
	cmd := exec.Command("bluetoothctl", "--timeout", fmt.Sprintf("%d", timeoutSec), "scan", "on")
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("bluetoothctl scan: %w", err)
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
