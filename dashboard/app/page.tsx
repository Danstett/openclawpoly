'use client'

import { useEffect, useState, useCallback } from 'react'

interface Position {
  // Support multiple field name formats from API
  question?: string
  title?: string
  market_id?: string
  marketId?: string
  side?: string
  outcome?: string
  shares?: number
  shares_yes?: number
  shares_no?: number
  sharesYes?: number
  sharesNo?: number
  avg_price?: number
  avgPrice?: number
  average_price?: number
  avg_price_yes?: number
  avg_price_no?: number
  current_price?: number
  currentPrice?: number
  current_price_yes?: number
  current_price_no?: number
  value?: number
  cost?: number
  pnl?: number
  profit?: number
  pnl_percent?: number
  resolved?: boolean
  won?: boolean
}

interface Trade {
  id?: string
  trade_id?: string
  tradeId?: string
  market_id?: string
  marketId?: string
  question?: string
  title?: string
  side?: string
  outcome?: string
  amount?: number
  cost?: number
  shares?: number
  shares_bought?: number
  sharesBought?: number
  price?: number
  avg_price?: number
  created_at?: string
  createdAt?: string
  timestamp?: string
  source?: string
  pnl?: number
  profit?: number
  resolved?: boolean
  won?: boolean
}

interface ApiLog {
  timestamp: string
  endpoint: string
  status: string
  data?: any
}

export default function Dashboard() {
  const [balance, setBalance] = useState<number>(0)
  const [positions, setPositions] = useState<Position[]>([])
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [apiLogs, setApiLogs] = useState<ApiLog[]>([])
  const [showDebug, setShowDebug] = useState(false)
  const [rawData, setRawData] = useState<any>({})

  const addLog = useCallback((endpoint: string, status: string, data?: any) => {
    setApiLogs(prev => [{
      timestamp: new Date().toLocaleTimeString(),
      endpoint,
      status,
      data
    }, ...prev.slice(0, 19)])
  }, [])

  const fetchData = useCallback(async () => {
    try {
      // Fetch all data in parallel
      const [portfolioRes, positionsRes, historyRes] = await Promise.all([
        fetch('/api/portfolio'),
        fetch('/api/positions'),
        fetch('/api/history'),
      ])

      const portfolioData = await portfolioRes.json()
      const positionsData = await positionsRes.json()
      const historyData = await historyRes.json()

      // Store raw data for debugging
      setRawData({ portfolio: portfolioData, positions: positionsData, history: historyData })

      // Log API responses
      addLog('/api/portfolio', portfolioRes.ok ? 'OK' : 'ERROR', portfolioData)
      addLog('/api/positions', positionsRes.ok ? 'OK' : 'ERROR', positionsData)
      addLog('/api/history', historyRes.ok ? 'OK' : 'ERROR', historyData)

      // Extract balance (try multiple field names)
      const bal = portfolioData?.balance_usdc ?? portfolioData?.balance ?? portfolioData?.balanceUsdc ?? 0
      setBalance(bal)

      if (portfolioData?.error) {
        setError(portfolioData.error)
      } else {
        setError(null)
      }

      // Extract positions (handle different response formats)
      let posArray: Position[] = []
      if (Array.isArray(positionsData)) {
        posArray = positionsData
      } else if (positionsData?.positions) {
        posArray = positionsData.positions
      } else if (positionsData?.data) {
        posArray = positionsData.data
      }
      setPositions(posArray)

      // Extract trades (handle different response formats)
      let tradesArray: Trade[] = []
      if (Array.isArray(historyData)) {
        tradesArray = historyData
      } else if (historyData?.trades) {
        tradesArray = historyData.trades
      } else if (historyData?.data) {
        tradesArray = historyData.data
      }
      setTrades(tradesArray)

      setLastUpdated(new Date())
    } catch (err) {
      setError(String(err))
      addLog('fetch', 'ERROR', String(err))
    } finally {
      setLoading(false)
    }
  }, [addLog])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 15000) // Refresh every 15s
    return () => clearInterval(interval)
  }, [fetchData])

  // Helper to get position details regardless of field names
  const getPositionDetails = (pos: Position) => {
    const question = pos.question || pos.title || 'Unknown Market'
    const side = pos.side || pos.outcome || (pos.shares_yes || pos.sharesYes ? 'yes' : 'no')
    const shares = pos.shares || pos.shares_yes || pos.shares_no || pos.sharesYes || pos.sharesNo || 0
    const avgPrice = pos.avg_price || pos.avgPrice || pos.average_price || pos.avg_price_yes || pos.avg_price_no || 0
    const currentPrice = pos.current_price || pos.currentPrice || pos.current_price_yes || pos.current_price_no || avgPrice
    const value = pos.value || pos.cost || (shares * currentPrice) || 0
    const cost = pos.cost || (shares * avgPrice) || 0
    const pnl = pos.pnl || pos.profit || (value - cost) || 0
    const pnlPercent = pos.pnl_percent || (cost > 0 ? ((pnl / cost) * 100) : 0)
    const isResolved = pos.resolved ?? false
    const won = pos.won
    
    return { question, side, shares, avgPrice, currentPrice, value, pnl, pnlPercent, isResolved, won }
  }

  // Helper to get trade details regardless of field names
  const getTradeDetails = (trade: Trade) => {
    const id = trade.id || trade.trade_id || trade.tradeId || ''
    const question = trade.question || trade.title || trade.market_id || trade.marketId || 'Unknown'
    const side = trade.side || trade.outcome || '?'
    const amount = trade.amount || trade.cost || 0
    const shares = trade.shares || trade.shares_bought || trade.sharesBought || 0
    const price = trade.price || trade.avg_price || (shares > 0 ? amount / shares : 0)
    const timestamp = trade.created_at || trade.createdAt || trade.timestamp || ''
    const pnl = trade.pnl || trade.profit
    const isResolved = trade.resolved ?? false
    const won = trade.won
    
    return { id, question, side, amount, shares, price, timestamp, pnl, isResolved, won }
  }

  // Filter for fast market positions
  const fastPositions = positions.filter(p => {
    const q = (p.question || p.title || '').toLowerCase()
    return q.includes('up or down') || q.includes('updown')
  })

  // Calculate totals
  const totalValue = fastPositions.reduce((sum, p) => sum + (getPositionDetails(p).value), 0)
  const totalPnL = fastPositions.reduce((sum, p) => sum + (getPositionDetails(p).pnl), 0)

  // Filter trades for fast markets
  const fastTrades = trades.filter(t => {
    const q = (t.question || t.title || '').toLowerCase()
    return q.includes('up or down') || q.includes('updown') || !t.question // include trades without question
  })

  const formatCurrency = (value: number | undefined | null) => {
    if (value === undefined || value === null || isNaN(value)) return '$0.00'
    const sign = value < 0 ? '-' : ''
    return `${sign}$${Math.abs(value).toFixed(2)}`
  }

  const formatPercent = (value: number | undefined | null) => {
    if (value === undefined || value === null || isNaN(value)) return '0%'
    const sign = value >= 0 ? '+' : ''
    return `${sign}${value.toFixed(1)}%`
  }

  const formatTime = (dateStr: string | undefined) => {
    if (!dateStr) return ''
    try {
      const date = new Date(dateStr)
      return date.toLocaleString('en-US', { 
        month: 'short', 
        day: 'numeric',
        hour: 'numeric', 
        minute: '2-digit',
        hour12: true 
      })
    } catch {
      return dateStr
    }
  }

  const formatShortTime = (dateStr: string | undefined) => {
    if (!dateStr) return ''
    try {
      const date = new Date(dateStr)
      return date.toLocaleTimeString('en-US', { 
        hour: 'numeric', 
        minute: '2-digit',
        hour12: true 
      })
    } catch {
      return dateStr
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a]">
        <div className="text-xl text-gray-400">Loading dashboard...</div>
      </div>
    )
  }

  return (
    <main className="min-h-screen p-4 md:p-8 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl md:text-3xl font-bold">FastLoop Dashboard</h1>
        <div className="flex items-center gap-2 text-sm text-gray-500">
          {lastUpdated && <span>Updated: {lastUpdated.toLocaleTimeString()}</span>}
          <button 
            onClick={fetchData}
            className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-white transition"
          >
            Refresh
          </button>
          <button 
            onClick={() => setShowDebug(!showDebug)}
            className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-white transition"
          >
            {showDebug ? 'Hide' : 'Debug'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-900/50 border border-red-700 rounded-lg">
          <p className="text-red-300">{error}</p>
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4 mb-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6">
          <div className="text-xs md:text-sm text-gray-500 mb-1">Wallet Balance</div>
          <div className="text-xl md:text-2xl font-bold text-green-400">
            {formatCurrency(balance)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6">
          <div className="text-xs md:text-sm text-gray-500 mb-1">Positions Value</div>
          <div className="text-xl md:text-2xl font-bold text-white">
            {formatCurrency(totalValue)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6">
          <div className="text-xs md:text-sm text-gray-500 mb-1">Total P&L</div>
          <div className={`text-xl md:text-2xl font-bold ${totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {formatCurrency(totalPnL)}
          </div>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6">
          <div className="text-xs md:text-sm text-gray-500 mb-1">Positions / Trades</div>
          <div className="text-xl md:text-2xl font-bold text-white">
            {fastPositions.length} / {fastTrades.length}
          </div>
        </div>
      </div>

      {/* Active Positions */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6 mb-6">
        <h2 className="text-lg md:text-xl font-semibold mb-4">Active Positions</h2>
        {fastPositions.length === 0 ? (
          <p className="text-gray-500">No active positions</p>
        ) : (
          <div className="space-y-2">
            {fastPositions.map((pos, i) => {
              const { question, side, shares, avgPrice, currentPrice, value, pnl, pnlPercent, isResolved, won } = getPositionDetails(pos)
              return (
                <div key={i} className={`p-3 rounded-lg border ${isResolved ? 'bg-gray-800/30 border-gray-700' : 'bg-gray-800/50 border-gray-700'}`}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{question}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          side.toLowerCase() === 'yes' || side.toLowerCase() === 'up' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {side.toUpperCase()}
                        </span>
                        <span className="text-xs text-gray-400">{shares.toFixed(1)} shares @ {formatCurrency(avgPrice)}</span>
                        {isResolved && (
                          <span className={`px-2 py-0.5 rounded text-xs ${won ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'}`}>
                            {won ? 'WON' : 'LOST'}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-medium">{formatCurrency(value)}</div>
                      <div className={`text-xs ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {formatCurrency(pnl)} ({formatPercent(pnlPercent)})
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Recent Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6 mb-6">
        <h2 className="text-lg md:text-xl font-semibold mb-4">Recent Trades</h2>
        {fastTrades.length === 0 ? (
          <p className="text-gray-500">No recent trades</p>
        ) : (
          <div className="space-y-2">
            {fastTrades.slice(0, 20).map((trade, i) => {
              const { id, question, side, amount, shares, price, timestamp, pnl, isResolved, won } = getTradeDetails(trade)
              return (
                <div key={i} className={`p-3 rounded-lg border ${isResolved ? 'bg-gray-800/30 border-gray-700' : 'bg-gray-800/50 border-gray-700'}`}>
                  <div className="flex justify-between items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm truncate">{question}</div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                          side.toLowerCase() === 'yes' || side.toLowerCase() === 'up' 
                            ? 'bg-green-900/50 text-green-400' 
                            : 'bg-red-900/50 text-red-400'
                        }`}>
                          {side.toUpperCase()}
                        </span>
                        <span className="text-xs text-gray-500">{formatShortTime(timestamp)}</span>
                        {isResolved && won !== undefined && (
                          <span className={`px-2 py-0.5 rounded text-xs ${won ? 'bg-green-900/50 text-green-400' : 'bg-red-900/50 text-red-400'}`}>
                            {won ? 'WON' : 'LOST'}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-sm">{formatCurrency(amount)}</div>
                      <div className="text-xs text-gray-400">{shares.toFixed(1)} @ {formatCurrency(price)}</div>
                      {pnl !== undefined && (
                        <div className={`text-xs ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {formatCurrency(pnl)}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Debug Panel */}
      {showDebug && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 md:p-6 mb-6">
          <h2 className="text-lg font-semibold mb-4">Debug Info</h2>
          
          {/* API Logs */}
          <div className="mb-4">
            <h3 className="text-sm font-medium text-gray-400 mb-2">API Calls</h3>
            <div className="bg-black/50 rounded p-3 max-h-40 overflow-y-auto font-mono text-xs">
              {apiLogs.map((log, i) => (
                <div key={i} className={`${log.status === 'OK' ? 'text-green-400' : 'text-red-400'}`}>
                  [{log.timestamp}] {log.endpoint}: {log.status}
                </div>
              ))}
            </div>
          </div>

          {/* Raw Data */}
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-2">Raw API Response</h3>
            <pre className="bg-black/50 rounded p-3 max-h-60 overflow-auto font-mono text-xs text-gray-300">
              {JSON.stringify(rawData, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="text-center text-xs text-gray-600">
        <p>Bot running on Railway • Auto-refresh every 15s • Click Debug to see API data</p>
      </div>
    </main>
  )
}
