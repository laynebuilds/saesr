import { CLOUD, MOCK } from './config'
import { createCloudSource } from './cloud'
import { createLiveSource } from './live'
import { createMockSource } from './mock'
import type { Source } from './source'

export const source: Source = CLOUD ? createCloudSource() : MOCK ? createMockSource() : createLiveSource()
