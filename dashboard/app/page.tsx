'use client'

import { useEffect, useState } from 'react'

interface Position {
  question?: string
  market_id?: string
  shares_yes?: number
  shares_no?: number
  avg_price_yes?: number
  avg_price_no?: number
  current_price_yes?: number
  current_price_no?: number
  value?: number
  pnl?: number
  pnl_percent?: number
}

interface Portfolio {
  balance_usdc?: number
  total_value?: number
  total_pnl?: number
  positions_count?: number
}

interface Trade {
  id?: string
  market_id?: string
  question?: string
  side?: string
  amount?: number
  shares?: number
  price?: number
  created_at?: string
  source?: string
}

export default function Dashboard() {
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchData = async () => {
    try {
      const [portfolioRes, positionsRes, historyRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/positions'),
        fetch('/api/history'),
      ])

      const portfolioData = await portfolioRes.json()
      const positionsData = await positionsRes.json()
      const historyData = await historyRes.json()

      if (portfolioData.error && !portfolioData.balance_usdc) {
        setError(portfolioData.error)
      } else {
        setPortfolio(portfolioData)
        setError(null)
      }

      // Handle positions array
      const posArray = positionsData.positions || positionsData || []
      setPositions(Array.isArray(posArray) ? posArray : [])

      // Handle trades array
      const tradesArray = historyData.trades || historyData || []
      setTrades(Array.isArray(tradesArray) ? tradesArray : [])

      setLastUpdated(new Date())
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000) // Refresh every 30s
    return () => clearInterval(interval)
  }, [])

  const formatCurrency = (value: number | undefined) => {
    if (value === undefined || value === null) return '$0.00'
    return `$${value.toFixed(2)}`
  }

  const formatPercent = (value: number | undefined) => {
    if (value === undefined || value === null) return '0%'
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(1)}%`
  }

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return ''
    const date = new Date(dateStr)
    return date.toLocaleString()
  }

  // Filter for fast market positions only
  const fastMarketPositions = positions.filter(p => 
    (p.question || '').toLowerCase().includes('up or down')
  )

  // Calculate total position value
  const totalPositionValue = fastMarketPositions.reduce((sum, p) => sum + (p.value || 0), 0)
  const totalPnL = fastMarketPositions.reduce((sum, p) => sum + (p.pnl || 0), 0)

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-xl text-gray-400">Loading...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-8">
        <h1 className="text-3xl font-bold">FastLoop Dashboard</h1>
        <div className="text-sm text-gray-500">
          {lastUpdated && `Updated: ${lastUpdated.toLocaleTimeString()}`}
          <button 
            onClick={fetchData}
            className="ml-4 px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-white"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-900/50 border border-red-700 rounded-lg">
          <p className="text-red-300">{error}</p>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <div className="text-sm text-gray-500 mb-1">Wallet Balance</div>
          <div className="text-2xl font-bold text-green-400">
            {formatCurrency(portfolio?.balance_usdc)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <div className="text-sm text-gray-500 mb-1">Positions Value</div>
          <div className="text-2xl font-bold">
            {formatCurrency(totalPositionValue)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <div className="text-sm text-gray-500 mb-1">Total P&L</div>
          <div className={`text-2xl font-bold ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {formatCurrency(totalPnL)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
          <div className="text-sm text-gray-500 mb-1">Active Positions</div>
          <div className="text-2xl font-bold">
            {fastMarketPositions.length}
          </div>
        </div>
      </div>

      {/* Positions */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 mb-8">
        <h2 className="text-xl font-semibold mb-4">Active Positions</h2>
        {fastMarketPositions.length === 0 ? (
          <p className="text-gray-500">No active fast market positions</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left text-gray-500 text-sm border-b border-gray-800">
                  <th className="pb-3">Market</th>
                  <th className="pb-3">Side</th>
                  <th className="pb-3">Shares</th>
                  <th className="pb-3">Avg Price</th>
                  <th className="pb-3">Current</th>
                  <th className="pb-3">Value</th>
                  <th className="pb-3">P&L</th>
                </tr>
              </thead>
              <tbody>
                {fastMarketPositions.map((pos, i) => {
                  const isYes = (pos.shares_yes || 0) > 0
                  const shares = isYes ? pos.shares_yes : pos.shares_no
                  const avgPrice = isYes ? pos.avg_price_yes : pos.avg_price_no
                  const currentPrice = isYes ? pos.current_price_yes : pos.current_price_no
                  
                  return (
                    <tr key={i} className="border-b border-gray-800/50">
                      <td className="py-3 pr-4">
                        <div className="max-w-xs truncate" title={pos.question}>
                          {pos.question || 'Unknown Market'}
                        </div>
                      </td>
                      <td className="py-3">
                        <span className={`px-2 py-1 rounded text-sm ${
                          isYes ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                        }`}>
                          {isYes ? 'YES' : 'NO'}
                        </span>
                      </td>
                      <td className="py-3">{shares?.toFixed(1) || '0'}</td>
                      <td className="py-3">{formatCurrency(avgPrice)}</td>
                      <td className="py-3">{formatCurrency(currentPrice)}</td>
                      <td className="py-3">{formatCurrency(pos.value)}</td>
                      <td className={`py-3 ${(pos.pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {formatCurrency(pos.pnl)} ({formatPercent(pos.pnl_percent)})
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6">
        <h2 className="text-xl font-semibold mb-4">Recent Trades</h2>
        {trades.length === 0 ? (
          <p className="text-gray-500">No recent trades</p>
        ) : (
          <div className="space-y-3">
            {trades.slice(0, 10).map((trade, i) => (
              <div key={i} className="flex justify-between items-center p-3 bg-gray-800/50 rounded">
                <div>
                  <div className="text-sm text-gray-400 truncate max-w-md">
                    {trade.question || trade.market_id || 'Unknown'}
                  </div>
                  <div className="text-xs text-gray-600">
                    {formatTime(trade.created_at)}
                  </div>
                </div>
                <div className="text-right">
                  <span className={`px-2 py-1 rounded text-sm ${
                    trade.side === 'yes' ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'
                  }`}>
                    {trade.side?.toUpperCase() || '?'}
                  </span>
                  <div className="text-sm mt-1">
                    {formatCurrency(trade.amount)} ({trade.shares?.toFixed(1) || '?'} shares)
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Bot Status */}
      <div className="mt-8 text-center text-sm text-gray-600">
        <p>Bot running on Railway • Refreshes every 30s</p>
      </div>
    </main>
  )
}
