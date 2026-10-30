package app.cpuminer

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Foreground-сервис с постоянным уведомлением. Запускается только по кнопке пользователя
 * и запускает xmrig как дочерний процесс.
 */
class MinerService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var process: Process? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var monitor: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopMining("Остановлено пользователем"); return START_NOT_STICKY }
        if (process == null) startMining()
        return START_NOT_STICKY // не перезапускаем сами: майнинг только по явной команде
    }

    private fun startMining() {
        val cfg = MinerConfig.load(this)
        val bin = File(applicationInfo.nativeLibraryDir, "libxmrig.so")
        if (!bin.exists()) { fail("Не найден libxmrig.so (см. README)"); return }
        if (!cfg.isValid) { fail("Укажите пул и адрес кошелька"); return }

        startForeground(NOTIF_ID, notification("Запуск…"))
        wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "cpuminer:mining").apply { acquire() }

        val conf = File(filesDir, "config.json").apply { writeText(cfg.toXmrigJson(HTTP_PORT)) }
        process = ProcessBuilder(bin.absolutePath, "-c", conf.absolutePath, "--no-color")
            .redirectErrorStream(true).start()
        MinerState.update { it.copy(running = true, status = "Подключение к пулу…") }

        scope.launch {
            process?.inputStream?.bufferedReader()?.forEachLine { MinerState.log(it.take(200)) }
            if (process != null) stopMining("Процесс xmrig завершился")
        }
        monitor = scope.launch { monitorLoop(cfg) }
    }

    private suspend fun monitorLoop(cfg: MinerConfig) {
        delay(5_000)
        while (scope.isActive) {
            val snap = SafetyMonitor.snapshot(this)
            MinerState.update { it.copy(batteryPct = snap.pct, tempC = snap.tempC, charging = snap.charging) }
            SafetyMonitor.stopReason(cfg, snap)?.let { stopMining(it); return }

            fetchStats()?.let { s ->
                MinerState.update { it.copy(hashrate = s.first, accepted = s.second, rejected = s.third, status = "Майнинг") }
                notify("${"%.1f".format(s.first)} H/s · ${snap.tempC}°C")
            }
            delay(5_000)
        }
    }

    /** Локальный HTTP API xmrig: (hashrate, accepted, rejected). */
    private fun fetchStats(): Triple<Double, Long, Long>? = runCatching {
        val c = URL("http://127.0.0.1:$HTTP_PORT/2/summary").openConnection() as HttpURLConnection
        c.connectTimeout = 1500; c.readTimeout = 1500
        val j = JSONObject(c.inputStream.bufferedReader().readText())
        val hr = j.getJSONObject("hashrate").getJSONArray("total").optDouble(0, 0.0)
        val res = j.getJSONObject("results")
        val bad = res.optLong("shares_total") - res.optLong("shares_good")
        Triple(hr, res.optLong("shares_good"), bad)
    }.getOrNull()

    private fun stopMining(reason: String) {
        monitor?.cancel()
        process?.let { p -> process = null; p.destroy() }
        wakeLock?.takeIf { it.isHeld }?.release()
        MinerState.update { it.copy(running = false, status = reason, hashrate = 0.0) }
        MinerState.log(reason)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun fail(msg: String) {
        MinerState.update { it.copy(running = false, status = msg) }
        stopSelf()
    }

    override fun onDestroy() {
        process?.destroy(); process = null
        wakeLock?.takeIf { it.isHeld }?.release()
        scope.cancel()
        super.onDestroy()
    }

    private fun notify(text: String) =
        (getSystemService(NOTIFICATION_SERVICE) as NotificationManager).notify(NOTIF_ID, notification(text))

    private fun notification(text: String): Notification {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Майнинг", NotificationManager.IMPORTANCE_LOW))
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(
            this, 1, Intent(this, MinerService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Майнинг запущен").setContentText(text)
            .setContentIntent(open).setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Стоп", stop).build())
            .build()
    }

    companion object {
        const val ACTION_STOP = "app.cpuminer.STOP"
        private const val CHANNEL = "mining"
        private const val NOTIF_ID = 1
        private const val HTTP_PORT = 18080

        fun start(ctx: Context) = ctx.startForegroundService(Intent(ctx, MinerService::class.java))
        fun stop(ctx: Context) = ctx.startService(Intent(ctx, MinerService::class.java).setAction(ACTION_STOP))
    }
}
