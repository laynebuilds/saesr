import { MOCK } from './config'
import { createLiveSource } from './live'
import { createMockSource } from './mock'
import type { Source } from './source'

export const source: Source = MOCK ? createMockSource() : createLiveSource()
