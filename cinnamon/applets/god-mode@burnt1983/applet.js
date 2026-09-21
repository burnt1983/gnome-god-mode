const Applet = imports.ui.applet;
const Util = imports.misc.util;

function WidgetApplet(metadata, orientation, panelHeight, instanceId) {
    this._init(metadata, orientation, panelHeight, instanceId);
}
WidgetApplet.prototype = {
    __proto__: Applet.IconApplet.prototype,
    _init: function(metadata, orientation, panelHeight, instanceId) {
        Applet.IconApplet.prototype._init.call(this, orientation, panelHeight, instanceId);
        this._cmd = metadata.panel_command || metadata.command;
        this.set_applet_tooltip(metadata.name);
        try {
            this.set_applet_icon_name(metadata.icon || "preferences-system");
        } catch (e) {}
    },
    on_applet_clicked: function() {
        Util.spawnCommandLine(this._cmd);
    }
};
function main(metadata, orientation, panelHeight, instanceId) {
    return new WidgetApplet(metadata, orientation, panelHeight, instanceId);
}
