package com.kemal.jarvis.tools

import org.json.JSONArray
import org.json.JSONObject

/** Gemini işlev tanımı yardımcıları. */
object ToolDecl {
    fun str(desc: String, enum: List<String>? = null): JSONObject =
        JSONObject().put("type", "STRING").put("description", desc).also { o -> enum?.let { o.put("enum", JSONArray(it)) } }

    fun int(desc: String) = JSONObject().put("type", "INTEGER").put("description", desc)
    fun bool(desc: String) = JSONObject().put("type", "BOOLEAN").put("description", desc)
    fun strList(desc: String) = JSONObject().put("type", "ARRAY").put("description", desc).put("items", JSONObject().put("type", "STRING"))

    fun fn(name: String, description: String, props: Map<String, JSONObject> = emptyMap(), required: List<String> = emptyList()): JSONObject {
        val o = JSONObject().put("name", name).put("description", description)
        if (props.isNotEmpty()) {
            val p = JSONObject()
            props.forEach { (k, v) -> p.put(k, v) }
            val params = JSONObject().put("type", "OBJECT").put("properties", p)
            if (required.isNotEmpty()) params.put("required", JSONArray(required))
            o.put("parameters", params)
        }
        return o
    }

    /** Sonuç iletisi: modele giden kısa JSON. */
    fun result(status: String, message: String, extra: Map<String, Any?> = emptyMap()): JSONObject {
        val o = JSONObject().put("status", status).put("message", message)
        extra.forEach { (k, v) -> if (v != null) o.put(k, v) }
        return o
    }
}
