package main

import (
	"os"

	"group-chat-app/internal/loadbalancer"
)

func main() {
	os.Exit(loadbalancer.RunCLI(os.Args[1:], os.Stdout, os.Stderr))
}
