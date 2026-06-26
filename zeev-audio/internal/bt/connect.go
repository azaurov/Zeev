package bt

import (
	"fmt"
	"log"
	"os/exec"
	"strings"
	"time"
)

// Connect pairs (if needed), trusts, and connects to mac.
// After connecting, refreshes the BT audio globals via Detect.
func Connect(mac string) error {
	mac = strings.ToUpper(strings.TrimSpace(mac))
	if mac == "" {
		return fmt.Errorf("empty MAC address")
	}
	// Trust + connect in one bluetoothctl invocation.
	script := fmt.Sprintf("trust %s\nconnect %s\nquit\n", mac, mac)
	cmd := exec.Command("bluetoothctl")
	cmd.Stdin = strings.NewReader(script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("bt: connect %s output: %s", mac, out)
		return fmt.Errorf("bluetoothctl connect: %w", err)
	}
	// Give the stack a moment to negotiate A2DP.
	time.Sleep(2 * time.Second)
	status := Detect(2)
	if !status.Connected {
		return fmt.Errorf("bt: connected to %s but no BlueALSA PCM detected", mac)
	}
	log.Printf("bt: connected %s -> %s", mac, status.Dev)
	return nil
}

// Disconnect disconnects mac and clears the BT audio globals.
func Disconnect(mac string) error {
	mac = strings.ToUpper(strings.TrimSpace(mac))
	script := fmt.Sprintf("disconnect %s\nquit\n", mac)
	cmd := exec.Command("bluetoothctl")
	cmd.Stdin = strings.NewReader(script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("bt: disconnect %s output: %s", mac, out)
		return fmt.Errorf("bluetoothctl disconnect: %w", err)
	}
	mu.Lock()
	audioDev = ""
	btRate = 0
	btChannels = 0
	mu.Unlock()
	log.Printf("bt: disconnected %s", mac)
	return nil
}

// Pair pairs and trusts mac without connecting.
func Pair(mac string) error {
	mac = strings.ToUpper(strings.TrimSpace(mac))
	script := fmt.Sprintf("pair %s\ntrust %s\nquit\n", mac, mac)
	cmd := exec.Command("bluetoothctl")
	cmd.Stdin = strings.NewReader(script)
	out, err := cmd.CombinedOutput()
	if err != nil {
		log.Printf("bt: pair %s output: %s", mac, out)
		return fmt.Errorf("bluetoothctl pair: %w", err)
	}
	log.Printf("bt: paired %s", mac)
	return nil
}
