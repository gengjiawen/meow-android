package io.github.madeye.meow.ui.screens.providers

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProxyEditFieldStateTest {
    private val profiles = listOf(
        """
            proxies:
              - { name: Local, type: snell, server: test.example, port: 443, psk: test-secret, udp: true, obfs-opts: { mode: http, host: edge.example }, future-option: old-value, dialer-proxy: DIRECT }
        """.trimIndent(),
        """
            proxies:
              - name: Local
                type: snell
                server: test.example
                port: 443
                psk: test-secret
                udp: true
                obfs-opts:
                  mode: http
                  host: edge.example
                future-option: old-value
                dialer-proxy: DIRECT
        """.trimIndent(),
    )

    @Test
    fun `clearing optional text removes saved keys and keeps the dialer`() {
        profiles.forEach { yaml ->
            var state = initialState(yaml)
            state = updateProxyEditField(state, "obfs-opts.host", "")
            state = updateProxyEditField(state, "future-option", "")
            assertTrue(state.dirty)
            // The Advanced control remains editable after erasing its last character.
            assertEquals("", state.fields.single { it.key == "future-option" }.value)
            val saved = save(yaml, state)
            assertFalse(saved.contains("host:"))
            assertFalse(saved.contains("future-option:"))
            val reopened = parseProxyEntry(saved, "Local")!!.fields
            assertEquals("DIRECT", reopened.last().value)
            assertEquals("http", reopened.single { it.key == "obfs-opts.mode" }.value)
        }
    }

    @Test
    fun `typing into a cleared Advanced field saves its replacement`() {
        profiles.forEach { yaml ->
            var state = updateProxyEditField(initialState(yaml), "future-option", "")
            state = updateProxyEditField(state, "future-option", "replacement")
            val reopened = parseProxyEntry(save(yaml, state), "Local")!!.fields
            assertEquals("replacement", reopened.single { it.key == "future-option" }.value)
        }
    }

    @Test
    fun `turning a switch off persists false without a delete action`() {
        profiles.forEach { yaml ->
            val state = updateProxyEditField(initialState(yaml), "udp", "false")
            val reopened = parseProxyEntry(save(yaml, state), "Local")!!.fields
            val udp = reopened.single { it.key == "udp" }
            assertTrue(udp.present)
            assertEquals("false", udp.value)
            assertEquals("DIRECT", reopened.last().value)
        }
    }

    @Test
    fun `clearing required text keeps the field for validation`() {
        profiles.forEach { yaml ->
            val state = updateProxyEditField(initialState(yaml), "psk", "")
            val field = state.fields.single { it.key == "psk" }
            assertTrue(field.required)
            assertTrue(field.present)
            assertFalse(state.removedKeys.contains("psk"))
            assertEquals("", field.value)
        }
    }

    private fun initialState(yaml: String): ProxyEditUiState {
        val parsed = parseProxyEntry(yaml, "Local")!!
        return ProxyEditUiState(
            name = "Local",
            type = parsed.type,
            fields = parsed.fields,
            initialFields = parsed.fields,
            loading = false,
        )
    }

    private fun save(yaml: String, state: ProxyEditUiState): String = replaceProxyEntry(
        yaml = yaml,
        name = state.name,
        fields = state.fields.associate { it.key to it.value },
        kinds = state.fields.associate { it.key to it.kind },
        presence = state.fields.associate { it.key to it.present },
        removed = state.removedKeys + state.fields.filter { !it.present }.map { it.key },
    )!!
}
