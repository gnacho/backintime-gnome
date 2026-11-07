"""Timeline scrubber: one tick per snapshot plus the 'now' end point.

Custom widget because the design needs a thin rail with ticks and a
draggable handle, which no stock GTK widget offers. Geometry is derived
from the snapshot count with a power curve so recent points sit closer
together on the right, like in the prototype.
"""
import math

import gi

gi.require_version('Gtk', '4.0')
from gi.repository import GObject, Gtk  # noqa: E402


class Timeline(Gtk.DrawingArea):
    __gsignals__ = {
        # index into the chronological list, or None for "now"
        'seek': (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    HEIGHT = 34

    def __init__(self):
        super().__init__()
        self.set_content_height(self.HEIGHT)
        self.set_hexpand(True)
        self._points = []          # normalised positions, oldest -> newest
        self._current = None       # index or None
        self._time_mode = False
        self._handle_drag = False

        self.set_draw_func(self._draw)
        self.add_controller(self._drag())
        self.add_controller(self._click())

    # ------------------------------------------------------------- layout
    def set_count(self, n):
        self._points = [_position(i, n) for i in range(n)] if n else []
        self.queue_draw()

    def set_current(self, index):
        self._current = index
        self.queue_draw()

    def set_time_mode(self, on):
        self._time_mode = on
        self.queue_draw()

    def _position_of(self, index):
        if index is None:
            return 1.0
        if 0 <= index < len(self._points):
            return self._points[index]
        return 1.0

    def _x_of(self, p, width):
        pad = 10
        return pad + p * max(width - 2 * pad, 1)

    def _index_from_x(self, x):
        width = max(self.get_width(), 1)
        pad = 10
        p = (x - pad) / max(width - 2 * pad, 1)
        p = min(1.0, max(0.0, p))
        if p > 0.965:
            return None
        best, distance = 0, 9.0
        for i, q in enumerate(self._points):
            d = abs(q - p)
            if d < distance:
                best, distance = i, d
        return best

    # ------------------------------------------------------------ gestures
    def _click(self):
        gesture = Gtk.GestureClick()
        gesture.connect('pressed', self._on_pressed)
        return gesture

    def _drag(self):
        gesture = Gtk.GestureDrag()
        gesture.connect('drag-begin', self._on_drag_begin)
        gesture.connect('drag-update', self._on_drag_update)
        gesture.connect('drag-end', self._on_drag_end)
        return gesture

    def _on_pressed(self, _g, _n, x, _y):
        self.emit('seek', self._index_from_x(x))

    def _on_drag_begin(self, _g, _x, _y):
        self._handle_drag = True

    def _on_drag_update(self, gesture, offset_x, _offset_y):
        if not self._handle_drag:
            return
        ok, start_x, _start_y = gesture.get_start_point()
        if not ok:
            return
        self.emit('seek', self._index_from_x(start_x + offset_x))

    def _on_drag_end(self, _g, _x, _y):
        self._handle_drag = False

    # -------------------------------------------------------------- drawing
    def _draw(self, area, cr, width, height):
        if width <= 0:
            return
        if self._time_mode:
            track = (1, 1, 1, 0.20)
            dim = (0.95, 0.93, 1, 0.45)
            accent = (0.957, 0.447, 0.714, 1)   # #f472b6
            fill_b = (0.078, 0.722, 0.651, 1)   # #14b8a6
            handle_border = (0.086, 0.051, 0.173, 1)
        else:
            track = (0.19, 0.17, 0.38, 0.16)
            dim = (0.19, 0.17, 0.38, 0.45)
            accent = (0.427, 0.329, 0.847, 1)   # #6d54d8
            fill_b = (0.078, 0.722, 0.651, 1)
            handle_border = (1, 1, 1, 1)

        rail_y = 15
        x0 = self._x_of(0, width)
        x1 = self._x_of(1, width)
        self._rounded(cr, x0, rail_y, x1 - x0, 4, 2, track)

        pos = self._position_of(self._current)
        if pos > 0:
            grad = _linear_gradient(cr, x0, 0, x1, 0)
            grad.add_color_stop_rgba(0, *accent[:3], 0.55)
            grad.add_color_stop_rgba(1, *fill_b[:3], 0.45)
            cr.set_source(grad)
            self._rounded(cr, x0, rail_y, (x1 - x0) * pos, 4, 2, None)

        for i, p in enumerate(self._points):
            x = self._x_of(p, width)
            recent = i >= len(self._points) - 6
            cr.set_source_rgba(*dim) if not recent else cr.set_source_rgba(*dim[:3], 0.8)
            h = 20 if recent else 14
            y = rail_y + 2 - (h - 4) / 2
            cr.rectangle(x - 1, y, 2, h)
            cr.fill()

        hx = self._x_of(pos, width)
        cr.set_source_rgba(*accent)
        cr.arc(hx, rail_y + 2, 9, 0, 2 * math.pi)
        cr.fill_preserve()
        cr.set_source_rgba(*handle_border)
        cr.set_line_width(3)
        cr.stroke()

    @staticmethod
    def _rounded(cr, x, y, w, h, radius, color):
        if w <= 0:
            return
        radius = min(radius, h / 2, w / 2)
        cr.new_sub_path()
        cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
        cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        if color:
            cr.set_source_rgba(*color)
        cr.fill()


def _position(i, n):
    if n <= 0:
        return 0.0
    return (i / n) ** 1.35 * 0.94


def _linear_gradient(cr, x0, y0, x1, y1):
    import cairo
    return cairo.LinearGradient(x0, y0, x1, y1)
