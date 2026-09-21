import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Without this each render stays in the document, and the second test in a
// file gets "found multiple elements" for markup the first one left behind —
// a failure that looks like a component bug and is not one.
afterEach(cleanup)
