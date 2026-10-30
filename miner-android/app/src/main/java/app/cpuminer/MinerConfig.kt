package app.cpuminer

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/** Настройки майнера. Хранятся в SharedPreferences, кошелёк и пул вводит пользователь. */
data class MinerConfig(
    val pool: String = "pool.supportxmr.com:3333",
    val wallet: String = "",
    val worker: String = "phone",
    val threads: Int = 2,
    val maxTempC: Int = 42,
    val minBatteryPct: Int = 30,
    val onlyWhileCharging: Boolean = true,
) {
    val isValid get() = wallet.length >= 90 && pool.contains(":")

    fun toXmrigJson(httpPort: Int): String = JSONObject().apply {
        put("autosave", false)
        put("http", JSONObject().apply {
            put("enabled", true); put("host", "127.0.0.1"); put("port", httpPort)
            put("restricted", true)
        })
        put("cpu", JSONObject().apply {
            put("enabled", true)
            put("max-threads-hint", 100)
            // Ограничиваем число потоков явным списком профилей rx.
            put("rx", JSONArray().apply { repeat(threads) { put(-1) } })
        })
        put("randomx", JSONObject().apply { put("1gb-pages", false); put("mode", "light") })
        put("pools", JSONArray().put(JSONObject().apply {
            put("url", pool)
            put("user", wallet)
            put("pass", worker)
            put("keepalive", true)
            put("tls", false)
        }))
    }.toString(2)

    fun save(ctx: Context) = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE).edit()
        .putString("pool", pool).putString("wallet", wallet).putString("worker", worker)
        .putInt("threads", threads).putInt("maxTemp", maxTempC)
        .putInt("minBat", minBatteryPct).putBoolean("charging", onlyWhileCharging).apply()

    companion object {
        fun load(ctx: Context): MinerConfig {
            val p = ctx.getSharedPreferences("cfg", Context.MODE_PRIVATE)
            val d = MinerConfig()
            return MinerConfig(
                pool = p.getString("pool", d.pool)!!,
                wallet = p.getString("wallet", d.wallet)!!,
                worker = p.getString("worker", d.worker)!!,
                threads = p.getInt("threads", d.threads),
                maxTempC = p.getInt("maxTemp", d.maxTempC),
                minBatteryPct = p.getInt("minBat", d.minBatteryPct),
                onlyWhileCharging = p.getBoolean("charging", d.onlyWhileCharging),
            )
        }
    }
}
