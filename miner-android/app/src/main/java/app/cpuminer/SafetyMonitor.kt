package app.cpuminer

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager

data class BatterySnapshot(val pct: Int, val tempC: Float, val charging: Boolean)

/** Читает состояние батареи и решает, можно ли продолжать майнинг. */
object SafetyMonitor {
    fun snapshot(ctx: Context): BatterySnapshot {
        val i: Intent? = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, 0) ?: 0
        val scale = i?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val temp = (i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0) / 10f
        val status = i?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
            status == BatteryManager.BATTERY_STATUS_FULL
        return BatterySnapshot(level * 100 / scale, temp, charging)
    }

    /** Возвращает причину остановки или null, если всё в порядке. */
    fun stopReason(cfg: MinerConfig, s: BatterySnapshot): String? = when {
        s.tempC >= cfg.maxTempC -> "Перегрев: ${s.tempC}°C"
        s.pct < cfg.minBatteryPct && !s.charging -> "Низкий заряд: ${s.pct}%"
        cfg.onlyWhileCharging && !s.charging -> "Зарядка отключена"
        else -> null
    }
}
