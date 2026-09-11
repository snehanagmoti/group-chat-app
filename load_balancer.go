//go:build ignore

// Compatibility entry point. The implementation lives in internal/loadbalancer
// so that "go build ./..." can build both Go commands without duplicate mains.
package main

import (
	"os"

	"group-chat-app/internal/loadbalancer"
)

func main() {
	os.Exit(loadbalancer.RunCLI(os.Args[1:], os.Stdout, os.Stderr))
}
