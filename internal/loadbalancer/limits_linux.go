//go:build linux

package loadbalancer

import "syscall"

func raiseOpenFileLimit() error {
	var limit syscall.Rlimit
	if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &limit); err != nil {
		return err
	}
	target := uint64(65535)
	if limit.Max < target {
		target = limit.Max
	}
	if limit.Cur >= target {
		return nil
	}
	limit.Cur = target
	return syscall.Setrlimit(syscall.RLIMIT_NOFILE, &limit)
}
