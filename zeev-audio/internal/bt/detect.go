// Package bt handles BlueALSA device detection and connection management.
// All functions shell out to bluealsa-aplay and bluetoothctl — hardware-only.
package bt

import (
	"fmt"
	"log"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/azaurov/zeev-audio/internal/audio"
)

var (
	mu         sync.RWMutex
	audioDev   string // e.g. "bluealsa:DEV=XX:XX,PROFILE=a2dp"
	btRate     int    // negotiated A2DP sample rate
	btChannels int    // negotiated A2DP channel count
)

// Status holds the current BT audio state.
type Status struct {
	Connected  bool
	Dev        string
	Rate       int
	Channels   int
}

// GetStatus returns a snapshot of the current BT audio state.
func GetStatus() Status {
	mu.RLock()
	defer mu.RUnlock()
	return Status{
		Connected:  audioDev != "",
		Dev:        audioDev,
		Rate:       btRate,
		Channels:   btChannels,
	}
}

// AudioDev returns the active ALSA PCM string if BT headphones are connected,
// or the wired speaker device otherwise.
func AudioDev() string {
	mu.RLock()
	d := audioDev
	mu.RUnlock()
	if d != "" {
		return d
	}
	return audio.DefaultSpeakerDev()
}

var pcmLineRe = regexp.MustCompile(`bluealsa:DEV=([0-9A-F:]+),PROFILE=a2dp`)
var rateRe = regexp.MustCompile(`(\d+)\s*Hz`)
var chRe = regexp.MustCompile(`(\d+)\s*ch(?:annel)?`)

// Detect queries bluealsa-aplay --list-pcms and refreshes the BT globals.
// Retries up to retries times with 1s sleep (headphones are optional at boot).
func Detect(retries int) Status {
	for attempt := 0; attempt <= retries; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Second)
		}
		out, err := exec.Command("bluealsa-aplay", "--list-pcms").Output()
		if err != nil {
			continue
		}
		if status, ok := parsePCMs(out); ok {
			mu.Lock()
			audioDev = status.Dev
			btRate = status.Rate
			btChannels = status.Channels
			mu.Unlock()
			log.Printf("bt: detected %s rate=%d ch=%d", status.Dev, status.Rate, status.Channels)
			return status
		}
	}
	// Nothing found — clear globals.
	mu.Lock()
	audioDev = ""
	btRate = 0
	btChannels = 0
	mu.Unlock()
	return Status{}
}

// Verify re-queries bluealsa-aplay on every TTS call to detect disconnects.
func Verify() Status {
	out, err := exec.Command("bluealsa-aplay", "--list-pcms").Output()
	if err != nil {
		mu.Lock()
		audioDev = ""
		btRate = 0
		btChannels = 0
		mu.Unlock()
		return Status{}
	}
	status, ok := parsePCMs(out)
	mu.Lock()
	if ok {
		audioDev = status.Dev
		btRate = status.Rate
		btChannels = status.Channels
	} else {
		audioDev = ""
		btRate = 0
		btChannels = 0
	}
	mu.Unlock()
	return status
}

// parsePCMs reads `bluealsa-aplay --list-pcms` output, where each PCM spans
// three lines, e.g.:
//
//	bluealsa:DEV=EC:81:93:AF:4B:98,PROFILE=a2dp,SRV=org.bluealsa
//	    Jaybird Vista, trusted audio-headphones, playback
//	    A2DP (SBC): S16_LE 2 channels 48000 Hz
//
// The rate/channel count are on the codec description line, not the
// device header line — they must be read from the lines that follow.
func parsePCMs(data []byte) (Status, bool) {
	lines := strings.Split(string(data), "\n")
	for i, line := range lines {
		m := pcmLineRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		dev := fmt.Sprintf("bluealsa:DEV=%s,PROFILE=a2dp", m[1])
		rate := 44100
		ch := 2
		for j := i + 1; j < len(lines) && j < i+4; j++ {
			if pcmLineRe.MatchString(lines[j]) {
				break // next device's header — no codec line found for this one
			}
			if rm := rateRe.FindStringSubmatch(lines[j]); rm != nil {
				rate, _ = strconv.Atoi(rm[1])
			}
			if cm := chRe.FindStringSubmatch(lines[j]); cm != nil {
				ch, _ = strconv.Atoi(cm[1])
			}
		}
		return Status{Connected: true, Dev: dev, Rate: rate, Channels: ch}, true
	}
	return Status{}, false
}
