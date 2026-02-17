import { NextResponse } from 'next/server'

const SIMMER_BASE = process.env.SIMMER_API_BASE || 'https://api.simmer.markets'

export async function GET() {
  const apiKey = process.env.SIMMER_API_KEY
  
  if (!apiKey) {
    return NextResponse.json({ error: 'SIMMER_API_KEY not configured' }, { status: 500 })
  }

  try {
    const res = await fetch(`${SIMMER_BASE}/api/sdk/portfolio`, {
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'User-Agent': 'fastloop-dashboard/1.0',
      },
      cache: 'no-store',
    })

    if (!res.ok) {
      const error = await res.text()
      return NextResponse.json({ error }, { status: res.status })
    }

    const data = await res.json()
    return NextResponse.json(data)
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 })
  }
}
