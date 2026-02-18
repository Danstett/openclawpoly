import { NextResponse } from 'next/server'

const DATA_API = 'https://data-api.polymarket.com'

export async function GET() {
  const walletAddress = process.env.POLYMARKET_WALLET_ADDRESS

  if (!walletAddress) {
    return NextResponse.json({ error: 'POLYMARKET_WALLET_ADDRESS not configured' }, { status: 500 })
  }

  try {
    const res = await fetch(
      `${DATA_API}/positions?user=${walletAddress}&sizeThreshold=0.1&limit=500`,
      { cache: 'no-store' }
    )

    if (!res.ok) {
      return NextResponse.json({ error: `Data API error: ${res.status}` }, { status: res.status })
    }

    const positions = await res.json()
    const positionsList = Array.isArray(positions) ? positions : []

    // Calculate totals from positions
    const totalPnl = positionsList.reduce((sum: number, p: any) => sum + (parseFloat(p.cashPnl) || 0), 0)
    const totalValue = positionsList.reduce((sum: number, p: any) => sum + (parseFloat(p.currentValue) || 0), 0)

    return NextResponse.json({
      balance_usdc: 0, // USDC balance requires on-chain query; shown via Python bot
      total_exposure: totalValue,
      positions_count: positionsList.length,
      pnl_total: totalPnl,
    })
  } catch (error) {
    return NextResponse.json({ error: String(error) }, { status: 500 })
  }
}
