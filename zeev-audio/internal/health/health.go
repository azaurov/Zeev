// Package health provides system health metrics from /proc and PiSugar.
package health

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

// Stats holds a health snapshot.
type Stats struct {
	Load1    float64 `json:"load1"`
	Load5    float64 `json:"load5"`
	Load15   float64 `json:"load15"`
	MemTotal int64   `json:"mem_total_kb"`
	MemFree  int64   `json:"mem_free_kb"`
	MemAvail int64   `json:"mem_avail_kb"`
	Battery  int     `json:"battery_pct"`  // -1 if PiSugar not present
}

// Collect reads /proc/loadavg and /proc/meminfo, and optionally queries PiSugar.
func Collect() Stats {
	s := Stats{Battery: -1}
	s.Load1, s.Load5, s.Load15 = loadAvg()
	s.MemTotal, s.MemFree, s.MemAvail = memInfo()
	s.Battery = pisugarBattery()
	return s
}

func loadAvg() (float64, float64, float64) {
	data, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return 0, 0, 0
	}
	fields := strings.Fields(string(data))
	if len(fields) < 3 {
		return 0, 0, 0
	}
	l1, _ := strconv.ParseFloat(fields[0], 64)
	l5, _ := strconv.ParseFloat(fields[1], 64)
	l15, _ := strconv.ParseFloat(fields[2], 64)
	return l1, l5, l15
}

func memInfo() (total, free, avail int64) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		val, _ := strconv.ParseInt(fields[1], 10, 64)
		switch {
		case strings.HasPrefix(line, "MemTotal:"):
			total = val
		case strings.HasPrefix(line, "MemFree:"):
			free = val
		case strings.HasPrefix(line, "MemAvailable:"):
			avail = val
		}
	}
	return
}

// pisugarBattery queries the PiSugar2 power daemon on its Unix socket.
// Returns -1 if the daemon is not running or the device is absent.
func pisugarBattery() int {
	socket := "/tmp/pisugar-server.sock"
	conn, err := net.DialTimeout("unix", socket, 500*time.Millisecond)
	if err != nil {
		return -1
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(time.Second))
	fmt.Fprintln(conn, "get battery")
	buf := make([]byte, 64)
	n, err := conn.Read(buf)
	if err != nil {
		return -1
	}
	// Response: "battery: 87.5\n"
	resp := strings.TrimSpace(string(buf[:n]))
	parts := strings.SplitN(resp, ":", 2)
	if len(parts) < 2 {
		return -1
	}
	f, err := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
	if err != nil {
		return -1
	}
	return int(f)
}
