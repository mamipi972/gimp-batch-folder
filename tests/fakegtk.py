# -*- coding: utf-8 -*-
"""
Faux GTK 3 / GDK : assez de surface pour construire ``bfp.ui.BatchDialog``
sans serveur X.

Ce module ne valide évidemment pas l'API GTK elle-même — il valide notre
code : que chaque réglage est bien relié à un widget, que l'aller-retour
réglages → widgets → réglages est fidèle, et que la logique de
sensibilité/désactivation fait ce qu'on croit.

Les widgets qui portent une valeur (cases, champs, listes, sélecteurs de
fichier, boutons de couleur, compteurs) ont un vrai état ; les autres sont
des coquilles permissives.
"""

from __future__ import annotations

import types


class _Enum(object):
    def __init__(self, name):
        self._name = name

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        value = "%s.%s" % (self._name, item)
        setattr(self, item, value)
        return value


class StyleContext(object):
    def __init__(self):
        self.classes = set()

    def add_class(self, name):
        self.classes.add(name)


class Widget(object):
    """Base permissive : tout ``set_*`` inconnu est accepté sans effet."""

    def __init__(self, *args, **kwargs):
        self._sensitive = True
        self._visible = False
        self._handlers = {}
        self._style = StyleContext()
        self.children = []
        self._kwargs = kwargs

    # -- état réellement utilisé par les tests
    def set_sensitive(self, value):
        self._sensitive = bool(value)

    def get_sensitive(self):
        return self._sensitive

    def get_style_context(self):
        return self._style

    def connect(self, signal, callback, *args):
        self._handlers.setdefault(signal, []).append(callback)
        return len(self._handlers[signal])

    def emit(self, signal, *args):
        for callback in self._handlers.get(signal, []):
            callback(self, *args)

    # -- conteneurs
    def add(self, child):
        self.children.append(child)

    def pack_start(self, child, *args):
        self.children.append(child)

    def pack_end(self, child, *args):
        self.children.append(child)

    def attach(self, child, *args):
        self.children.append(child)

    def show_all(self):
        self._visible = True
        for child in self.children:
            try:
                child.show_all()
            except Exception:
                pass

    def destroy(self):
        pass

    # -- tout le reste : accepté et ignoré
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kwargs: None


class Label(Widget):
    def __init__(self, *args, **kwargs):
        super(Label, self).__init__(*args, **kwargs)
        self._text = ""

    def set_text(self, text):
        self._text = text

    def set_markup(self, markup):
        self._text = markup

    def get_text(self):
        return self._text


class Box(Widget):
    pass


class Grid(Widget):
    pass


class Frame(Widget):
    pass


class ScrolledWindow(Widget):
    pass


class Notebook(Widget):
    def __init__(self, *args, **kwargs):
        super(Notebook, self).__init__(*args, **kwargs)
        self.pages = []
        self.current = 0

    def append_page(self, child, label):
        self.pages.append((child, label))

    def get_n_pages(self):
        return len(self.pages)

    def set_current_page(self, index):
        self.current = index

    def get_current_page(self):
        return self.current


class Button(Widget):
    def __init__(self, label="", **kwargs):
        super(Button, self).__init__(**kwargs)
        self.label = kwargs.get("label", label)

    def clicked(self):
        self.emit("clicked")


class ToggleLike(Widget):
    def __init__(self, *args, **kwargs):
        super(ToggleLike, self).__init__(*args, **kwargs)
        self.label = kwargs.get("label", args[0] if args else "")
        self._active = False

    def set_active(self, value):
        value = bool(value)
        if value != self._active:
            self._active = value
            self.emit("toggled")
        else:
            self._active = value

    def get_active(self):
        return self._active


class CheckButton(ToggleLike):
    pass


class RadioButton(ToggleLike):
    def __init__(self, *args, **kwargs):
        super(RadioButton, self).__init__(*args, **kwargs)
        self.group = [self]

    @staticmethod
    def new_with_label_from_widget(sibling, label):
        button = RadioButton(label=label)
        if sibling is not None:
            sibling.group.append(button)
            button.group = sibling.group
        else:
            button.group = [button]
        if len(button.group) == 1:
            button._active = True
        return button

    def set_active(self, value):
        if value:
            for other in self.group:
                if other is not self and other._active:
                    other._active = False
                    other.emit("toggled")
            if not self._active:
                self._active = True
                self.emit("toggled")
        else:
            self._active = False


class Entry(Widget):
    def __init__(self, *args, **kwargs):
        super(Entry, self).__init__(*args, **kwargs)
        self._text = ""

    def set_text(self, text):
        self._text = str(text)
        self.emit("changed")

    def get_text(self):
        return self._text


class ComboBoxText(Widget):
    def __init__(self, *args, **kwargs):
        super(ComboBoxText, self).__init__(*args, **kwargs)
        self.items = []          # [(id, texte)]
        self._active_id = None
        self._entry = None

    @staticmethod
    def new_with_entry():
        combo = ComboBoxText()
        combo._entry = Entry()
        return combo

    def get_child(self):
        return self._entry

    def append(self, identifier, text):
        # GTK ne sélectionne rien automatiquement : on reproduit ce
        # comportement, sinon les tests d'aller-retour seraient faussés.
        self.items.append((identifier, text))

    def append_text(self, text):
        self.append(text, text)

    def remove_all(self):
        self.items = []
        self._active_id = None

    def set_active_id(self, identifier):
        if any(i == identifier for i, _t in self.items):
            self._active_id = identifier
            self.emit("changed")
            return True
        return False

    def get_active_id(self):
        return self._active_id


class Adjustment(object):
    def __init__(self, value=0, lower=0, upper=100, step_increment=1,
                 page_increment=10):
        self.value = value
        self.lower = lower
        self.upper = upper


class SpinButton(Widget):
    def __init__(self, adjustment=None, climb_rate=1, digits=0, **kwargs):
        super(SpinButton, self).__init__(**kwargs)
        self.adjustment = adjustment or Adjustment()
        self.digits = digits
        self._value = float(self.adjustment.value)

    def set_value(self, value):
        value = max(self.adjustment.lower, min(self.adjustment.upper,
                                               float(value)))
        self._value = value
        self.emit("value-changed")

    def get_value(self):
        return self._value


class FileChooserButton(Widget):
    def __init__(self, title="", action=None, **kwargs):
        super(FileChooserButton, self).__init__(**kwargs)
        self.title = title
        self.action = action
        self._filename = None

    def set_filename(self, path):
        self._filename = path
        self.emit("file-set")
        return True

    def get_filename(self):
        return self._filename


class FileFilter(Widget):
    pass


class RGBA(object):
    def __init__(self):
        self.red = self.green = self.blue = 0.0
        self.alpha = 1.0


class ColorButton(Widget):
    def __init__(self, *args, **kwargs):
        super(ColorButton, self).__init__(*args, **kwargs)
        self._rgba = RGBA()

    def set_rgba(self, rgba):
        self._rgba = rgba
        self.emit("color-set")

    def get_rgba(self):
        return self._rgba


class TextBuffer(object):
    def __init__(self):
        self.text = ""

    def get_end_iter(self):
        return len(self.text)

    def insert(self, _iter, text):
        self.text += text

    def create_mark(self, *args):
        return object()

    def delete_mark(self, _mark):
        pass


class TextView(Widget):
    def __init__(self, buffer=None, **kwargs):
        super(TextView, self).__init__(**kwargs)
        self.buffer = buffer


class ProgressBar(Widget):
    def __init__(self, *args, **kwargs):
        super(ProgressBar, self).__init__(*args, **kwargs)
        self.fraction = 0.0
        self.text = ""

    def set_fraction(self, value):
        self.fraction = value

    def set_text(self, value):
        self.text = value

    def pulse(self):
        pass


class Dialog(Widget):
    def __init__(self, title="", use_header_bar=False, **kwargs):
        super(Dialog, self).__init__(**kwargs)
        self.title = title
        self.buttons = {}
        self._content = Box()

    def get_content_area(self):
        return self._content

    def add_button(self, label, response):
        button = Button(label=label)
        self.buttons[response] = button
        return button

    def response(self, response_id):
        self.emit("response", response_id)

    def run(self):
        return 0


class MessageDialog(Dialog):
    def __init__(self, **kwargs):
        super(MessageDialog, self).__init__(**kwargs)
        self.secondary = ""

    def format_secondary_text(self, text):
        self.secondary = text


class _Settings(object):
    @staticmethod
    def get_default():
        return _Settings()

    def get_property(self, _name):
        return False


GtkModule = types.SimpleNamespace(
    Settings=_Settings,
    Dialog=Dialog,
    MessageDialog=MessageDialog,
    Grid=Grid,
    Box=Box,
    Frame=Frame,
    Label=Label,
    Notebook=Notebook,
    ScrolledWindow=ScrolledWindow,
    Button=Button,
    CheckButton=CheckButton,
    RadioButton=RadioButton,
    Entry=Entry,
    ComboBoxText=ComboBoxText,
    Adjustment=Adjustment,
    SpinButton=SpinButton,
    FileChooserButton=FileChooserButton,
    FileFilter=FileFilter,
    ColorButton=ColorButton,
    TextBuffer=TextBuffer,
    TextView=TextView,
    ProgressBar=ProgressBar,
    Orientation=_Enum("Orientation"),
    PolicyType=_Enum("PolicyType"),
    ShadowType=_Enum("ShadowType"),
    WrapMode=_Enum("WrapMode"),
    FileChooserAction=_Enum("FileChooserAction"),
    ResponseType=_Enum("ResponseType"),
    ButtonsType=_Enum("ButtonsType"),
    MessageType=_Enum("MessageType"),
    events_pending=lambda: False,
    main_iteration_do=lambda blocking: False,
    main=lambda: None,
    main_quit=lambda: None,
)

GdkModule = types.SimpleNamespace(RGBA=RGBA)
