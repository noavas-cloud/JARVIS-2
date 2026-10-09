package com.kemal.jarvis.ui

import android.graphics.Paint
import android.graphics.Typeface
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableFloatState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import com.kemal.jarvis.conv.Conversation.Phase
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

/* Mac'teki JARVIS 2 küresinin (hud_render) renkleri. */
private val CYAN = Color(0xFF5FD0F0)        // dinliyor
private val GOLD = Color(0xFFFFCC6E)        // konuşuyor
private val ICE = Color(0xFFD6F0FA)         // düşünüyor
private val PALE = Color(0xFFAAD7E8)        // bağlanıyor
private val IDLE_C = Color(0xFF7E98A0)      // beklemede
private val BAND = Color(0xFF5CB2D3)
private val RING = Color(0xFF73C1CC)
private val YELLOW = Color(0xFFD6C052)
private val WORD = Color(0xFFE8F9FB)        // J.A.R.V.I.S. (Mac'teki yeni yazının rengi)
private val GLASS = Color(0xFF06131A)

/**
 * JARVIS 2 küresi — telefon sürümü (2.4.0, 03.10.2026). Mac'teki HUD küresine benzer ama mobil uygulama havasında
 * sadeleştirilmiş: doku yok, yumuşak ışıma, koyu cam disk, yuvarlak uçlu vektör halkalar.
 *
 *  • dış halka: bir çeyreği boş, ucu parlak; ~14 sn'de bir tur (düşünürken hızlanır)
 *  • kadran çentikleri: konuşurken son ~2 sn'lik ses tepeden iki yana yayılarak parlar
 *  • bölmeli bant (sol üstten sağ alta): dinlerken mikrofonla canlanır, düşünürken üzerinde parlak bölme gezer
 *  • solda sarı yay + tepede beş sarı nokta (Mac'teki küre gibi), içte ters dönen kesikli halka
 *  • düşünürken/bağlanırken radar taraması; beklerken tek kuyruklu nokta döner; konuşurken küre "nefes alır"
 *  • ortada geniş J.A.R.V.I.S. (Mac'teki yeni yazı gibi yatayda geniş, ışıyan); küre küçükken (klavye açık) çizilmez
 * Durum geçişleri yumuşaktır (renk 0,45 sn, hız/parlaklık üstel yumuşatma).
 */
@Composable
fun Orb(phase: Phase, level: Float, modifier: Modifier = Modifier) {
    val target = when (phase) {
        Phase.IDLE -> IDLE_C
        Phase.CONNECTING -> PALE
        Phase.LISTENING -> CYAN
        Phase.THINKING -> ICE
        Phase.SPEAKING -> GOLD
    }
    val accent by animateColorAsState(target, tween(450), label = "orb")
    val currentPhase by rememberUpdatedState(phase)
    val currentLevel by rememberUpdatedState(level)

    val time = remember { mutableFloatStateOf(0f) }
    val rot = remember { FloatArray(4) }                 // dış yay, kadran, iç kesikli halka, radar (derece)
    val busy = remember { mutableFloatStateOf(0f) }
    val listen = remember { mutableFloatStateOf(0f) }
    val speak = remember { mutableFloatStateOf(0f) }
    val idle = remember { mutableFloatStateOf(1f) }
    val lvl = remember { mutableFloatStateOf(0f) }
    val hist = remember { FloatArray(48) }               // konuşma ses geçmişi (~2 sn, en yeni [0])
    val histTimer = remember { mutableFloatStateOf(0f) }
    val paint = remember {
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.create(Typeface.DEFAULT, Typeface.BOLD)
            textScaleX = 1.5f                            // Mac'teki gibi yatayda geniş harfler
            textAlign = Paint.Align.CENTER
        }
    }

    LaunchedEffect(Unit) {
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                val dt = if (last == 0L) 0f else ((now - last) / 1e9f).coerceAtMost(0.05f)
                last = now
                time.floatValue += dt
                val p = currentPhase
                val k = min(1f, dt * 4f)
                fun ease(s: MutableFloatState, on: Boolean) {
                    s.floatValue += ((if (on) 1f else 0f) - s.floatValue) * k
                }
                ease(busy, p == Phase.THINKING || p == Phase.CONNECTING)
                ease(listen, p == Phase.LISTENING)
                ease(speak, p == Phase.SPEAKING)
                ease(idle, p == Phase.IDLE)
                val goal = currentLevel.coerceIn(0f, 1f)
                val rate = if (goal > lvl.floatValue) 14f else 5f
                lvl.floatValue += (goal - lvl.floatValue) * min(1f, dt * rate)
                val b = busy.floatValue
                val slow = 1f - 0.65f * idle.floatValue
                rot[0] = (rot[0] + dt * (360f / 14f) * (1f + 2.5f * b) * slow) % 360f
                rot[1] = (rot[1] + dt * 5f * (1f + 3f * b) * slow) % 360f
                rot[2] = (rot[2] - dt * 18f * (1f + 3f * b) * slow + 360f) % 360f
                rot[3] = (rot[3] + dt * 210f) % 360f
                histTimer.floatValue += dt
                if (histTimer.floatValue >= 1f / 24f) {
                    histTimer.floatValue = 0f
                    System.arraycopy(hist, 0, hist, 1, hist.size - 1)
                    hist[0] = if (p == Phase.SPEAKING) lvl.floatValue else hist[0] * 0.8f
                }
            }
        }
    }

    Canvas(modifier.fillMaxSize()) {
        val t = time.floatValue
        val b = busy.floatValue
        val li = listen.floatValue
        val sp = speak.floatValue
        val id = idle.floatValue
        val lv = lvl.floatValue
        val c = Offset(size.width / 2f, size.height / 2f)
        val tau = 2f * PI.toFloat()
        val breath = 1f + sp * (0.022f * sin(t * tau / 1.05f) + 0.02f * lv) + id * 0.012f * sin(t * tau / 4f)
        val r = min(size.width, size.height) * 0.42f * breath
        val bright = 1f - 0.35f * id
        val px = 1.dp.toPx()

        // 1) Arka ışıma ve koyu cam disk
        val glowA = (0.20f + 0.25f * lv * (sp + li) + 0.06f * b) * bright
        drawCircle(Brush.radialGradient(0.45f to accent.copy(alpha = glowA), 1f to Color.Transparent,
            center = c, radius = r * 1.38f), radius = r * 1.38f, center = c)
        drawCircle(Brush.radialGradient(0f to GLASS.copy(alpha = 0.96f), 0.82f to GLASS.copy(alpha = 0.92f),
            1f to accent.copy(alpha = 0.16f * bright), center = c, radius = r * 0.64f), radius = r * 0.64f, center = c)

        // 2) Radar taraması (düşünürken / bağlanırken)
        if (b > 0.03f) {
            rotate(rot[3], c) {
                drawArc(Brush.sweepGradient(0f to Color.Transparent, 0.86f to Color.Transparent,
                    1f to accent.copy(alpha = 0.32f * b), center = c), startAngle = 0f, sweepAngle = 360f,
                    useCenter = true, topLeft = c - Offset(r * 0.6f, r * 0.6f), size = Size(r * 1.2f, r * 1.2f))
                drawLine(accent.copy(alpha = 0.7f * b), c, c + Offset(r * 0.6f, 0f), strokeWidth = 1.2f * px,
                    cap = StrokeCap.Round)
            }
        }

        // 3) İç halkalar: düz + ters dönen kesikli
        ring(c, r * 0.64f, RING.copy(alpha = 0.55f * bright), 1.2f * px)
        rotate(rot[2], c) {
            for (i in 0 until 36) arc(c, r * 0.575f, i * 10f, 5.5f, accent.copy(alpha = 0.42f * bright), 1.4f * px)
        }

        // 4) Bölmeli bant: 210°'den (sol üst) 204° boyunca (sağ alta), 12° bölmeler
        val seg = 12f
        val gap = 2.2f
        val n = 17
        val hot = (t * 70f) % (n * seg)                    // düşünürken gezen parlak bölme
        for (i in 0 until n) {
            val wave = 0.5f + 0.5f * sin(t * 5f - i * 0.55f)
            var a = 0.34f + li * lv * (0.25f + 0.45f * wave) + sp * 0.08f
            if (b > 0.03f) {
                val d = abs(i * seg - hot)
                a += b * 0.5f * (1f - min(d, n * seg - d) / 30f).coerceIn(0f, 1f)
            }
            arc(c, r * 0.725f, 210f + i * seg + gap / 2, seg - gap,
                BAND.copy(alpha = (a * bright).coerceIn(0f, 0.95f)), r * 0.12f, StrokeCap.Butt)
        }

        // 5) Solda sarı yay + tepede beş sarı nokta
        arc(c, r * 0.845f, 148f, 64f, YELLOW.copy(alpha = 0.85f * bright), 2.4f * px)
        for (j in 0 until 5) {
            val ang = Math.toRadians(258.0 + j * 6.0).toFloat()
            drawCircle(YELLOW.copy(alpha = (0.95f - 0.12f * j) * bright), radius = (2.6f - 0.3f * j) * px,
                center = c + Offset(cos(ang) * r * 0.845f, sin(ang) * r * 0.845f))
        }

        // 6) Kadran çentikleri (konuşurken sesle parlar: en yeni ses tepede, geçmiş iki yana yayılır)
        rotate(rot[1], c) {
            for (i in 0 until 72) {
                val deg = i * 5f
                val fromTop = abs(((deg + rot[1] - 270f + 540f) % 360f) - 180f)   // ekranda tepeden uzaklık
                val v = if (sp > 0.03f) hist[min(hist.size - 1, (fromTop / 180f * hist.size).toInt())] * sp else 0f
                val long = i % 6 == 0
                val r0 = r * (if (long) 0.885f else 0.905f) - v * r * 0.05f
                val a = ((if (long) 0.55f else 0.32f) + 0.65f * v) * bright
                val rad = Math.toRadians(deg.toDouble())
                val u = Offset(cos(rad).toFloat(), sin(rad).toFloat())
                drawLine((if (v > 0.05f) accent else RING).copy(alpha = a.coerceIn(0f, 1f)), c + u * r0,
                    c + u * (r * 0.955f), strokeWidth = (if (long) 1.6f else 1.1f) * px, cap = StrokeCap.Round)
            }
        }

        // 7) Dış halka: soluk tam çember + bir çeyreği boş, ucu parlak dönen yay
        ring(c, r, RING.copy(alpha = 0.14f * bright), 1f * px)
        rotate(rot[0], c) {
            drawArc(Brush.sweepGradient(0f to accent.copy(alpha = 0f), 0.74f to accent.copy(alpha = 0.95f * bright),
                0.75f to accent.copy(alpha = 0f), 1f to accent.copy(alpha = 0f), center = c), startAngle = 0f,
                sweepAngle = 270f, useCenter = false, topLeft = c - Offset(r, r), size = Size(r * 2, r * 2),
                style = Stroke(width = 3f * px, cap = StrokeCap.Round))
            val tip = c + Offset(0f, -r)                 // yayın parlak ucu (270°)
            drawCircle(accent.copy(alpha = 0.35f * bright), radius = 6f * px, center = tip)
            drawCircle(Color.White.copy(alpha = 0.9f * bright), radius = 2.2f * px, center = tip)
        }

        // 8) Beklemede: tek kuyruklu nokta
        if (id > 0.03f) {
            for (j in 0 until 14) {
                val ang = Math.toRadians(t * 40.0 - j * 3.4).toFloat()
                drawCircle(Color(0xFFC8E1E8).copy(alpha = (1f - j / 14f) * id * 0.85f),
                    radius = (2.8f - 0.16f * j) * px,
                    center = c + Offset(cos(ang) * r * 0.845f, sin(ang) * r * 0.845f))
            }
        }

        // 9) J.A.R.V.I.S. (küçükken okunmaz → çizilmez)
        if (r > 56.dp.toPx()) {
            val cap = r * 0.094f                         // Mac'teki oran (yarıçapın %9,4'ü)
            paint.textSize = cap / 0.72f
            paint.color = lerp(GLASS, WORD, 0.98f * min(1f, bright + 0.15f)).toArgb()
            paint.setShadowLayer(r * 0.05f, 0f, 0f, accent.copy(alpha = 0.75f * bright).toArgb())
            drawContext.canvas.nativeCanvas.drawText("J.A.R.V.I.S.", c.x, c.y + cap / 2f - r * 0.01f, paint)
        }
    }
}

private fun lerp(a: Color, b: Color, f: Float) = Color(
    a.red + (b.red - a.red) * f, a.green + (b.green - a.green) * f, a.blue + (b.blue - a.blue) * f, 1f)

private fun DrawScope.ring(c: Offset, radius: Float, color: Color, width: Float) =
    drawCircle(color, radius = radius, center = c, style = Stroke(width = width))

private fun DrawScope.arc(c: Offset, radius: Float, start: Float, sweep: Float, color: Color, width: Float,
                          cap: StrokeCap = StrokeCap.Round) =
    drawArc(color, start, sweep, false, topLeft = c - Offset(radius, radius), size = Size(radius * 2, radius * 2),
        style = Stroke(width = width, cap = cap))
