package app.cpuminer

import android.Manifest
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { Surface { MinerScreen() } } }
    }
}

@Composable
fun MinerScreen() {
    val ctx = LocalContext.current
    val stats by MinerState.stats.collectAsStateWithLifecycle()
    var cfg by remember { mutableStateOf(MinerConfig.load(ctx)) }
    var consent by remember { mutableStateOf(false) }

    val notifPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) { notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp).statusBarsPadding(),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("CPU Miner (Monero / RandomX)", style = MaterialTheme.typography.titleLarge)

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(stats.status, style = MaterialTheme.typography.titleMedium)
                Text("Хешрейт: ${"%.1f".format(stats.hashrate)} H/s", fontSize = 28.sp)
                Text("Шары: принято ${stats.accepted} · отклонено ${stats.rejected}")
                Text("Батарея: ${stats.batteryPct}% · ${stats.tempC}°C · ${if (stats.charging) "заряжается" else "от батареи"}")
            }
        }

        OutlinedTextField(cfg.pool, { cfg = cfg.copy(pool = it.trim()) }, label = { Text("Пул (host:port)") },
            enabled = !stats.running, singleLine = true, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(cfg.wallet, { cfg = cfg.copy(wallet = it.trim()) }, label = { Text("Адрес кошелька XMR") },
            enabled = !stats.running, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(cfg.worker, { cfg = cfg.copy(worker = it.trim()) }, label = { Text("Имя воркера") },
            enabled = !stats.running, singleLine = true, modifier = Modifier.fillMaxWidth())

        val cores = remember { Runtime.getRuntime().availableProcessors() }
        Text("Потоки: ${cfg.threads} из $cores")
        Slider(cfg.threads.toFloat(), { cfg = cfg.copy(threads = it.toInt()) },
            valueRange = 1f..cores.toFloat(), steps = (cores - 2).coerceAtLeast(0), enabled = !stats.running)

        Text("Остановка при температуре: ${cfg.maxTempC}°C")
        Slider(cfg.maxTempC.toFloat(), { cfg = cfg.copy(maxTempC = it.toInt()) },
            valueRange = 35f..50f, enabled = !stats.running)

        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Switch(cfg.onlyWhileCharging, { cfg = cfg.copy(onlyWhileCharging = it) }, enabled = !stats.running)
            Spacer(Modifier.width(8.dp)); Text("Майнить только при зарядке")
        }

        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Checkbox(consent, { consent = it }, enabled = !stats.running)
            Text("Я понимаю: майнинг нагружает процессор, быстро разряжает и греет телефон, а доход минимален.",
                style = MaterialTheme.typography.bodySmall)
        }

        if (stats.running) {
            Button({ MinerService.stop(ctx) }, Modifier.fillMaxWidth()) { Text("Остановить") }
        } else {
            Button(
                { cfg.save(ctx); MinerService.start(ctx) },
                Modifier.fillMaxWidth(), enabled = consent && cfg.isValid,
            ) { Text("Запустить майнинг") }
            if (!cfg.isValid) Text("Введите корректный адрес XMR и пул", style = MaterialTheme.typography.bodySmall)
        }

        Text("Лог", style = MaterialTheme.typography.titleSmall)
        Text(stats.log.joinToString("\n"), fontFamily = FontFamily.Monospace, fontSize = 10.sp)
    }
}
