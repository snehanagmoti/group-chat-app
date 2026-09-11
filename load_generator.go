//go:build ignore

// Compatibility entry point. The implementation lives in internal/loadgenerator
// so that "go build ./..." can build both Go commands without duplicate mains.
package main

import (
	"os"

	"group-chat-app/internal/loadgenerator"
)

func main() {
	os.Exit(loadgenerator.RunCLI(os.Args[1:], os.Stdout, os.Stderr))
}
