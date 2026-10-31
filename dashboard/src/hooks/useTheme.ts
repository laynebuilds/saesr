import { useEffect, useState } from 'react'

// Dark is the default (command center). The choice is remembered per browser.
export type Theme = 'light' | 'dark'
const KEY = 'saesr-theme'

function read(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(read)
  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* private mode: theme still applies for this visit */
    }
  }, [theme])
  return { theme, setTheme }
}
