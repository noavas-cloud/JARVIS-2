package com.kemal.jarvis

import android.app.Activity
import android.os.Bundle
import android.util.Log

/** Debug: sahte sunucu adresi (live_url), onboarding atlama (skip_onboarding). Release derlemesinde yoktur. */
class DevActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = JarvisApp.of(this)
        intent.getStringExtra("live_url")?.let { app.settings.devLiveUrl = it }
        if (intent.getBooleanExtra("skip_onboarding", false)) app.settings.onboarded = true
        if (intent.getBooleanExtra("half_duplex", false)) app.settings.halfDuplex = true
        Log.i("JarvisDev", "live_url=${app.settings.devLiveUrl} onboarded=${app.settings.onboarded}")
        finish()
    }
}
