package main

import (
	"os"

	"group-chat-app/internal/loadgenerator"
)

func main() {
	os.Exit(loadgenerator.RunCLI(os.Args[1:], os.Stdout, os.Stderr))
}
