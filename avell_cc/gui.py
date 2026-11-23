"""Avell Control Center - interface GTK4/libadwaita."""

import sys
import threading
import csv
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from . import hw, curve  # noqa: E402

APP_ID = "br.com.avell.ControlCenterLinux"


def run_bg(window, fn, *args):
    """Executa uma escrita (pode abrir pkexec) fora da thread da UI."""
    if getattr(window, "_busy", False):
        return
    window._busy = True
    window.get_content().set_sensitive(False)
    def work():
        try:
            fn(*args)
            err = None
        except Exception as e:  # noqa: BLE001
            err = str(e)
        GLib.idle_add(window.after_write, err)
    threading.Thread(target=work, daemon=True).start()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Avell Control Center")
        self.set_default_size(1040, 820)
        css = Gtk.CssProvider()
        css.load_from_data(b".sensor-card { padding: 20px; border-radius: 16px; background: alpha(#3584e4, .09); } .sensor-card.cpu levelbar block.filled { background: #3584e4; } .sensor-card.gpu levelbar block.filled { background: #e5a50a; } .sensor-card.gpu { background: alpha(#e5a50a, .09); } .sensor-number { font-size: 36px; font-weight: 800; } .page-note { opacity: .65; font-size: 12px; } .curve-plot { border-radius: 12px; }")
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.history = deque(maxlen=900)
        self._kbd_timer = None
        self._updating = False

        self.toasts = Adw.ToastOverlay()
        stack = Adw.ViewStack()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        view.add_top_bar(header)
        view.set_content(stack)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        st = hw.status()
        if not st["driver"]:
            page = Adw.StatusPage(
                icon_name="dialog-warning-symbolic",
                title="Driver não carregado",
                description="Instale o projeto com ./install.sh ou rode\n"
                            "<tt>sudo modprobe avell_dchu</tt>")
            stack.add_titled_with_icon(page, "none", "Aviso",
                                       "dialog-warning-symbolic")
            return

        stack.add_titled_with_icon(self._page_monitor(), "monitor", "Monitor",
                                   "utilities-system-monitor-symbolic")
        stack.add_titled_with_icon(self._page_power(st), "power", "Energia",
                                   "power-profile-performance-symbolic")
        stack.add_titled_with_icon(self._page_curve(), "curve", "Curva",
                                   "preferences-system-symbolic")
        if st["kbd_type"] != "none":
            stack.add_titled_with_icon(self._page_keyboard(st), "kbd", "Teclado",
                                       "input-keyboard-symbolic")
        if "charge_end" in st:
            stack.add_titled_with_icon(self._page_battery(st), "bat", "Bateria",
                                       "battery-full-symbolic")
        stack.add_titled_with_icon(self._page_info(st), "info", "Sobre",
                                   "help-about-symbolic")

        self.refresh()
        self._timer = GLib.timeout_add_seconds(2, self._tick)
        self.connect("close-request", self._close)

    # ------------------------------------------------------------ pages
    def _page_monitor(self):
        page = Adw.PreferencesPage()
        self.sensor_group = Adw.PreferencesGroup(title="Seu notebook, em tempo real",
            description="Temperatura e ventilação • atualização a cada 2 segundos")
        page.add(self.sensor_group)
        self.sensor_rows = {}
        cards = Gtk.Box(spacing=16, homogeneous=True, margin_top=12, margin_bottom=16)
        for label, title in (("CPU", "Processador · CPU"), ("GPU", "Placa de vídeo · GPU")):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           css_classes=["sensor-card", label.lower()])
            card.append(Gtk.Label(label=title, xalign=0, css_classes=["heading"]))
            value = Gtk.Label(label="—", xalign=0, css_classes=["sensor-number", "numeric"])
            details = Gtk.Label(label="Aguardando leitura", xalign=0)
            bar = Gtk.LevelBar(min_value=0, max_value=100)
            bar.set_tooltip_text("Potência atual da ventoinha, em porcentagem")
            for widget in (value, details, bar): card.append(widget)
            self.sensor_rows[label] = (value, details, bar)
            cards.append(card)
        self.sensor_group.add(cards)
        self.monitor_state = Adw.ActionRow(title="Ventilação")
        self.sensor_group.add(self.monitor_state)
        history = Adw.PreferencesGroup(title="Como o notebook está se comportando",
            description="Histórico da sessão, até 30 minutos • CPU azul / GPU laranja")
        self.metric = Adw.ComboRow(title="Exibir", model=Gtk.StringList.new(
            ["Temperatura (°C)", "Rotação (RPM)", "Ventilação (%)"]))
        self.metric.connect("notify::selected", lambda *_: self.chart.queue_draw())
        history.add(self.metric)
        self.chart = Gtk.DrawingArea(content_height=230, hexpand=True)
        self.chart.set_draw_func(self._draw_history)
        history.add(self.chart)
        export = Gtk.Button(label="Exportar histórico em CSV", halign=Gtk.Align.END,
                            margin_top=12, margin_bottom=12)
        export.connect("clicked", self._export_history)
        history.add(export)
        page.add(history)
        return page

    def _page_power(self, st):
        page = Adw.PreferencesPage()

        g = Adw.PreferencesGroup(
            title="Modo de energia",
            description="Integrado ao menu de energia do GNOME")
        self.profile_buttons = {}
        first = None
        for key in st["profile_choices"]:
            row = Adw.ActionRow(title=hw.PROFILES.get(key, key))
            btn = Gtk.CheckButton(group=first)
            first = first or btn
            btn.connect("toggled", self._on_profile, key)
            row.add_prefix(btn)
            row.set_activatable_widget(btn)
            g.add(row)
            self.profile_buttons[key] = btn
        page.add(g)

        g = Adw.PreferencesGroup(title="Ventoinhas")
        self.fan_row = Adw.ComboRow(title="Modo das ventoinhas")
        self.fan_keys = [k for k in hw.FAN_MODES if k in st["fan_modes"]
                         and k != "custom"]
        self.fan_keys.append("curve")
        self.fan_row.set_model(Gtk.StringList.new(
            [hw.FAN_MODES.get(k, "Minha curva") for k in self.fan_keys]))
        self.fan_row.connect("notify::selected", self._on_fan)
        g.add(self.fan_row)
        dust = Adw.ActionRow(title="Limpeza anti-poeira",
                             subtitle="Executa a rotina anti-poeira do firmware")
        b = Gtk.Button(label="Executar", valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda *_: run_bg(self, hw.run_anti_dust))
        dust.add_suffix(b)
        g.add(dust)
        page.add(g)
        return page

    def _page_curve(self):
        page = Adw.PreferencesPage()
        info = Adw.PreferencesGroup(title="Equilibre silêncio e resfriamento",
            description="Edite os limites e aplique para salvar. A curva funciona com a janela fechada e volta ao iniciar.")
        page.add(info)
        saved = hw.load_state().get("curve", curve.DEFAULT)
        try:
            curve.validate(saved)
        except ValueError:
            saved = curve.DEFAULT
        self.curve_fields = {}
        preview = Adw.PreferencesGroup(title="Prévia da curva • CPU azul / GPU laranja",
            description="Proteção: a 90 °C, ambas vão a 100%, mesmo acima do máximo escolhido.")
        self.curve_chart = Gtk.DrawingArea(content_height=155, hexpand=True)
        self.curve_chart.set_draw_func(self._draw_curve)
        preview.add(self.curve_chart)
        page.add(preview)
        columns = Gtk.Box(spacing=20, homogeneous=True)
        limits = Adw.PreferencesGroup()
        limits.add(columns)
        page.add(limits)
        for label in ("CPU", "GPU"):
            group = Adw.PreferencesGroup(title=label)
            fields = []
            for title, low, high, value in zip(
                ("Temperatura inicial (°C)", "Temperatura final (°C)",
                 "Mínimo (%)", "Máximo (%)"),
                (30, 31, 30, 30), (84, 85, 100, 100), saved[label]):
                row = Adw.SpinRow.new_with_range(low, high, 1)
                row.set_title(title)
                row.set_value(value)
                row.connect("notify::value", lambda *_: self.curve_chart.queue_draw())
                group.add(row)
                fields.append(row)
            self.curve_fields[label] = fields
            columns.append(group)
        box = Gtk.Box(spacing=12, margin_top=12, margin_bottom=12)
        apply = Gtk.Button(label="Aplicar e salvar curva", css_classes=["suggested-action"])
        apply.connect("clicked", self._apply_curve)
        auto = Gtk.Button(label="Voltar ao automático")
        auto.connect("clicked", lambda *_: run_bg(self, hw.set_fan_mode, "auto"))
        maximum = Gtk.Button(label="Resfriar ao máximo")
        maximum.connect("clicked", lambda *_: run_bg(self, hw.set_fan_mode, "max"))
        for button in (apply, auto, maximum):
            box.append(button)
        info.add(box)
        return page

    def _draw_curve(self, area, cr, width, height):
        left, top, w, h = 40, 12, max(1, width - 58), height - 38
        cr.set_line_width(1)
        for v in (0, 50, 100):
            y = top + h * (1 - v / 100)
            cr.set_source_rgba(.5, .5, .5, .25)
            cr.move_to(left, y); cr.line_to(left + w, y); cr.stroke()
            cr.set_source_rgb(.5, .5, .5)
            cr.move_to(3, y + 4); cr.show_text(str(v) + "%")
        for t in (30, 45, 60, 75, 90, 100):
            cr.move_to(left + (t - 30) / 70 * w - 10, height - 5)
            cr.show_text(str(t) + " C")
        for label, color in (("CPU", (.2,.55,1)), ("GPU", (1,.55,.15))):
            if label not in self.curve_fields: continue
            values = [int(row.get_value()) for row in self.curve_fields[label]]
            if values[0] >= values[1] or values[2] > values[3]: continue
            cr.set_source_rgb(*color); cr.set_line_width(2)
            for temp in range(30, 101):
                x = left + (temp - 30) / 70 * w
                y = top + h * (1 - curve.target(temp, values) / 100)
                if temp == 30: cr.move_to(x, y)
                else: cr.line_to(x, y)
            cr.stroke()

    def _apply_curve(self, *_):
        config = {label: [int(row.get_value()) for row in rows]
                  for label, rows in self.curve_fields.items()}
        try:
            curve.validate(config)
        except ValueError as exc:
            self.toasts.add_toast(Adw.Toast(title=str(exc)))
            return
        run_bg(self, hw.set_curve, config)

    def _draw_history(self, area, cr, width, height):
        metric = ("temp", "rpm", "duty")[self.metric.get_selected()]
        points = list(self.history)
        maximum = 110 if metric == "temp" else 100 if metric == "duty" else max(
            7000, max((s.get("rpm") or 0 for _, ss in points for s in ss), default=0) * 1.1)
        left, top, w, h = 52, 16, max(1, width - 70), max(1, height - 48)
        cr.set_line_width(1)
        for fraction in (0, .25, .5, .75, 1):
            y = top + h * (1 - fraction)
            cr.set_source_rgba(.5, .5, .5, .25)
            cr.move_to(left, y); cr.line_to(left + w, y); cr.stroke()
            cr.set_source_rgb(.5, .5, .5)
            cr.move_to(4, y + 4); cr.show_text(str(round(maximum * fraction)))
        if not points:
            return
        end = points[-1][0]
        start = max(points[0][0], end - 1800)
        duration = max(10, end - start)
        for label, color in (("CPU", (.2, .55, 1)), ("GPU", (1, .55, .15))):
            cr.set_source_rgb(*color); cr.set_line_width(2)
            active = False
            for stamp, samples in points:
                value = next((s.get(metric) for s in samples if s["label"] == label), None)
                if value is None:
                    cr.stroke(); active = False; continue
                x = left + (stamp - start) / duration * w
                y = top + h * (1 - min(maximum, max(0, value)) / maximum)
                if active: cr.line_to(x, y)
                else: cr.move_to(x, y); active = True
            cr.stroke()
            if active:
                cr.arc(x, y, 3, 0, 6.283185307); cr.fill()
        cr.set_source_rgb(.5, .5, .5)
        cr.move_to(left, height - 8); cr.show_text(datetime.fromtimestamp(start).strftime("%H:%M:%S"))
        cr.move_to(left + w - 58, height - 8); cr.show_text("agora")

    def _export_history(self, *_):
        folder = Path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or Path.home()) / "Avell"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / datetime.now().strftime("sensores-%Y%m%d-%H%M%S.csv")
            with path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["horario", "sensor", "temperatura_c", "rpm", "ventilacao_pct"])
                for stamp, samples in self.history:
                    for sample in samples:
                        writer.writerow([datetime.fromtimestamp(stamp).isoformat(), sample["label"],
                                         sample["temp"], sample["rpm"], sample["duty"]])
            self.toasts.add_toast(Adw.Toast(title=f"Salvo: {path}"))
        except OSError as exc:
            self.toasts.add_toast(Adw.Toast(title=f"Não foi possível exportar: {exc}"))

    def _close(self, *_):
        GLib.source_remove(self._timer)
        if self._kbd_timer:
            GLib.source_remove(self._kbd_timer)
        return False

    def _page_keyboard(self, st):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Deixe o teclado do seu jeito",
            description="Cor, efeito e brilho são salvos automaticamente e restaurados ao iniciar. RGB de uma zona.")
        self.keyboard_preview = Gtk.DrawingArea(content_height=140, hexpand=True)
        self.keyboard_preview.set_draw_func(self._draw_keyboard)
        g.add(self.keyboard_preview)
        row = Adw.ActionRow(title="Brilho")
        self.kbd_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, st.get("kbd_max", 4), 1)
        self.kbd_scale.set_hexpand(True)
        self.kbd_scale.set_draw_value(True)
        self.kbd_scale.set_digits(0)
        self.kbd_scale.connect("value-changed", self._on_kbd)
        row.add_suffix(self.kbd_scale)
        g.add(row)

        if st["kbd_type"] in ("rgb", "perkey"):
            crow = Adw.ActionRow(title="Cor")
            self.color_btn = Gtk.ColorDialogButton(
                dialog=Gtk.ColorDialog(with_alpha=False),
                valign=Gtk.Align.CENTER)
            self.color_btn.connect("notify::rgba", self._on_color)
            crow.add_suffix(self.color_btn)
            g.add(crow)
            palette = Gtk.Box(spacing=8, margin_top=12, margin_bottom=12)
            for name, hexcolor in (("Vermelho", "e01b24"), ("Azul", "3584e4"),
                                   ("Verde", "33d17a"), ("Roxo", "9141ac"), ("Branco", "ffffff")):
                button = Gtk.Button(label=name)
                button.connect("clicked", self._preset_color, hexcolor)
                palette.append(button)
            g.add(palette)

            self.effect_row = Adw.ComboRow(
                title="Efeito",
                subtitle="Respirar e Piscar usam a cor escolhida")
            self.effect_keys = st.get("kbd_effects") or ["static"]
            self.effect_row.set_model(Gtk.StringList.new(
                [hw.EFFECT_LABELS.get(e, e) for e in self.effect_keys]))
            self.effect_row.connect("notify::selected", self._on_effect)
            g.add(self.effect_row)
        page.add(g)
        return page

    def _preset_color(self, button, hexcolor):
        color = Gdk.RGBA()
        color.parse("#" + hexcolor)
        self.color_btn.set_rgba(color)

    def _draw_keyboard(self, area, cr, width, height):
        color = self.color_btn.get_rgba() if hasattr(self, "color_btn") else Gdk.RGBA()
        brightness = self.kbd_scale.get_value() / max(1, self.kbd_scale.get_adjustment().get_upper())
        key_w = min(42, (width - 40) / 14)
        origin = (width - 14 * key_w) / 2
        for row in range(4):
            for col in range(14):
                x, y = origin + col * key_w, 16 + row * 28
                cr.set_source_rgba(color.red, color.green, color.blue, .12 + .75 * brightness)
                cr.rectangle(x + 2, y, key_w - 5, 23); cr.fill()
        cr.set_source_rgba(.5, .5, .5, .8)
        cr.move_to(origin, 138); cr.show_text("Previa da cor - os efeitos aparecem no teclado")

    def _page_battery(self, st):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="FlexiCharger",
            description="Com o carregador conectado, a bateria só volta a "
                        "carregar quando cair abaixo do início e para no limite.")
        self.charge_switch = Adw.SwitchRow(title="Proteger a bateria")
        self.charge_switch.connect("notify::active", self._on_charge)
        g.add(self.charge_switch)
        self.charge_start = Adw.SpinRow.new_with_range(0, 95, 5)
        self.charge_start.set_title("Iniciar carga abaixo de (%)")
        self.charge_end = Adw.SpinRow.new_with_range(50, 100, 5)
        self.charge_end.set_title("Parar carga em (%)")
        for r in (self.charge_start, self.charge_end):
            r.connect("notify::value", self._on_charge)
            g.add(r)
        page.add(g)
        return page

    def _page_info(self, st):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(title="Sistema")
        for title, val in (("Firmware do EC", st.get("ec_version")),
                           ("Teclado", st.get("kbd_type")),
                           ("Recursos (WMI)", st.get("features"))):
            row = Adw.ActionRow(title=title, subtitle=val or "—")
            row.set_subtitle_selectable(True)
            g.add(row)
        page.add(g)
        return page

    # ----------------------------------------------------------- state
    def _tick(self):
        self._refresh_sensors()
        return True

    def _refresh_sensors(self):
        try:
            samples = hw.sensors()
        except (OSError, ValueError):
            samples = []
        self.history.append((time.time(), samples))
        for value, details, bar in self.sensor_rows.values():
            value.set_label("—")
            details.set_label("Leitura indisponível")
            bar.set_value(0)
        for sensor in samples:
            if sensor["label"] not in self.sensor_rows: continue
            value, details, bar = self.sensor_rows[sensor["label"]]
            value.set_label(f"{sensor['temp']} °C")
            details.set_label(f"{sensor['rpm']:,} RPM  ·  {sensor['duty']}%".replace(",", "."))
            bar.set_value(sensor["duty"] or 0)
        status = hw.curve_status()
        if status.get("active"):
            message = "Curva personalizada ativa • proteção a 90 °C"
        elif status.get("error"):
            message = "Curva interrompida: " + status["error"]
        else:
            mode, _ = hw._selected(hw._read(f"{hw.PLATFORM_DEV}/fan_mode"))
            message = hw.FAN_MODES.get(mode, "Leitura indisponível")
        self.monitor_state.set_subtitle(message)
        self.chart.queue_draw()

    def refresh(self):
        st = hw.status()
        self._updating = True
        try:
            btn = self.profile_buttons.get(st.get("profile"))
            if btn:
                btn.set_active(True)
            if st.get("fan_mode") in self.fan_keys:
                self.fan_row.set_selected(self.fan_keys.index(st["fan_mode"]))
            if hasattr(self, "kbd_scale"):
                self.kbd_scale.set_value(st.get("kbd_brightness", 0))
            if hasattr(self, "color_btn") and st.get("kbd_color"):
                c = Gdk.RGBA()
                c.parse("#" + st["kbd_color"])
                self.color_btn.set_rgba(c)
            if hasattr(self, "effect_row") and st.get("kbd_effect") in self.effect_keys:
                self.effect_row.set_selected(self.effect_keys.index(st["kbd_effect"]))
            if hasattr(self, "charge_switch"):
                on = not (st["charge_start"] == 0 and st["charge_end"] == 100)
                self.charge_switch.set_active(on)
                self.charge_start.set_value(st["charge_start"] if on else 70)
                self.charge_end.set_value(st["charge_end"] if on else 90)
                self.charge_start.set_sensitive(on)
                self.charge_end.set_sensitive(on)
        finally:
            self._updating = False
        self._refresh_sensors()
        if hasattr(self, "keyboard_preview"):
            self.keyboard_preview.queue_draw()

    def after_write(self, err):
        self._busy = False
        self.get_content().set_sensitive(True)
        if err:
            self.toasts.add_toast(Adw.Toast(title=f"Erro: {err}"))
        if not err:
            self.toasts.add_toast(Adw.Toast(title="Aplicado e salvo para a próxima inicialização"))
        self.refresh()
        return False

    # -------------------------------------------------------- handlers
    def _on_profile(self, btn, key):
        if not self._updating and btn.get_active():
            run_bg(self, hw.set_profile, key)

    def _on_fan(self, row, _):
        if not self._updating:
            key = self.fan_keys[row.get_selected()]
            if key == "curve":
                run_bg(self, hw.set_curve, hw.load_state().get("curve", curve.DEFAULT))
            else:
                run_bg(self, hw.set_fan_mode, key)

    def _on_kbd(self, scale):
        if not self._updating:
            if self._kbd_timer:
                GLib.source_remove(self._kbd_timer)
            def apply():
                self._kbd_timer = None
                run_bg(self, hw.set_kbd_brightness, int(round(scale.get_value())))
                return False
            self._kbd_timer = GLib.timeout_add(250, apply)

    def _on_color(self, btn, _):
        if self._updating:
            return
        self.keyboard_preview.queue_draw()
        c = btn.get_rgba()
        hexc = "%02x%02x%02x" % (round(c.red * 255), round(c.green * 255),
                                 round(c.blue * 255))
        run_bg(self, hw.set_kbd_color, hexc)

    def _on_effect(self, row, _):
        if not self._updating:
            run_bg(self, hw.set_kbd_effect, self.effect_keys[row.get_selected()])

    def _on_charge(self, *_):
        if self._updating:
            return
        on = self.charge_switch.get_active()
        self.charge_start.set_sensitive(on)
        self.charge_end.set_sensitive(on)
        if on:
            start = int(self.charge_start.get_value())
            end = int(self.charge_end.get_value())
            if start >= end:
                return
        else:
            start, end = 0, 100
        run_bg(self, hw.set_charge_limits, start, end)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main():
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
