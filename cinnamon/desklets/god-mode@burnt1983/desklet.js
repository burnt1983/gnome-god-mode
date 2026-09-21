const Desklet = imports.ui.desklet;
const Util = imports.misc.util;

function WidgetDesklet(metadata, desklet_id) {
    this._init(metadata, desklet_id);
}
WidgetDesklet.prototype = {
    __proto__: Desklet.Desklet.prototype,
    _init: function(metadata, desklet_id) {
        Desklet.Desklet.prototype._init.call(this, metadata, desklet_id);
        Util.spawnCommandLine(metadata.command);
        this.actor.hide();
    }
};
function main(metadata, desklet_id) {
    return new WidgetDesklet(metadata, desklet_id);
}
