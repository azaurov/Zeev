package proto

import (
	"bufio"
	"encoding/json"
	"io"
)

// Request is a single command sent from Python → Go over the Unix socket.
// Fields are command-specific; unknown fields are ignored.
type Request struct {
	ID         string  `json:"id"`
	Cmd        string  `json:"cmd"`
	Text       string  `json:"text,omitempty"`
	Lang       string  `json:"lang,omitempty"`
	Dev        string  `json:"dev,omitempty"`
	Level      int     `json:"level,omitempty"`
	MAC        string  `json:"mac,omitempty"`
	Timeout    int     `json:"timeout,omitempty"`
	MaxSeconds float64 `json:"max_seconds,omitempty"`
	VAD        bool    `json:"vad,omitempty"`
	Query      string  `json:"query,omitempty"`
	Block      bool    `json:"block,omitempty"`
	Rate       int     `json:"rate,omitempty"` // target sample rate for speak_sco
	Voice      string  `json:"voice,omitempty"` // Kokoro voice override, e.g. "bf_emma"
}

// Response is sent back from Go → Python for each request.
type Response struct {
	ID      string      `json:"id"`
	OK      bool        `json:"ok"`
	Error   string      `json:"error,omitempty"`
	Dev     string      `json:"dev,omitempty"`
	Level   int         `json:"level,omitempty"`
	Devices []BTDevice  `json:"devices,omitempty"`
	WavB64  string      `json:"wav_b64,omitempty"`
	Title   string      `json:"title,omitempty"`
	// BT status fields
	Connected bool   `json:"connected,omitempty"`
	BTDev     string `json:"bt_dev,omitempty"`
	BTRate    int    `json:"bt_rate,omitempty"`
	BTChannels int   `json:"bt_channels,omitempty"`
}

// BTDevice is one entry from a BT scan result.
type BTDevice struct {
	MAC  string `json:"mac"`
	Name string `json:"name"`
}

// WriteResponse encodes resp as NDJSON and writes it to w.
func WriteResponse(w io.Writer, resp Response) error {
	b, err := json.Marshal(resp)
	if err != nil {
		return err
	}
	b = append(b, '\n')
	_, err = w.Write(b)
	return err
}

// ReadRequest decodes the next NDJSON line from r.
func ReadRequest(r *bufio.Reader) (Request, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return Request{}, err
	}
	var req Request
	if err := json.Unmarshal([]byte(line), &req); err != nil {
		return Request{}, err
	}
	return req, nil
}
