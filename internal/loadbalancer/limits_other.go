//go:build !linux

package loadbalancer

func raiseOpenFileLimit() error {
	return nil
}
