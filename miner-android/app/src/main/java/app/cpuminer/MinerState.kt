package app.cpuminer

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

data class MinerStats(
    val running: Boolean = false,
    val status: String = "Остановлен",
    val hashrate: Double = 0.0,
    val accepted: Long = 0,
    val rejected: Long = 0,
    val batteryPct: Int = 0,
    val tempC: Float = 0f,
    val charging: Boolean = false,
    val log: List<String> = emptyList(),
)

/** Общее состояние между сервисом и UI. */
object MinerState {
    private val _stats = MutableStateFlow(MinerStats())
    val stats: StateFlow<MinerStats> = _stats

    fun update(block: (MinerStats) -> MinerStats) { _stats.value = block(_stats.value) }

    fun log(line: String) = update { it.copy(log = (it.log + line).takeLast(60)) }
}
