// Home page setup code.
//
//      Connor Shugg

// Document globals
const content = document.getElementById("content");
const tab_anchors = document.getElementById("tab_anchors");

// Other globals
const hostname = window.location.hostname;
const css_check = "fas fa-check mdl-color-text--green-400";
const css_xmark = "fas fa-xmark mdl-color-text--red-400";
const css_loading = "fas fa-hourglass mdl-color-text--grey-600";

// Services
const services = [
    new Service("pihole", "Pi-hole",
                new URL(hostname, "2301", "/admin/index.php"),
                "fa fa-network-wired", "mdl-color-text--red-400"),
    new Service("jellyfin", "Jellyfin",
                new URL(hostname, "2302", "/NEED_TO_ACTUALLY_MAKE_THIS"),
                "fa fa-tv", "mdl-color-text--purple-400"),
];
const services_me = [
    new Service("lumen", "Lumen",
                new URL(hostname, "2350", "/"),
                "fas fa-lightbulb", "mdl-color-text--yellow-600"),
    new Service("warden", "Warden",
                new URL(hostname, "2351", "/"),
                "fas fa-shield", "mdl-color-text--blue-600")
];

// Service-specific globals
let lumen = null;

// Window-load function
window.onload = function()
{
    // TAB 1: OVERVIEW
    const tab_overview = new Tab("overview", true);
    tab_overview.set_title("Overview");
    content.appendChild(tab_overview.html);
    tab_anchors.appendChild(tab_overview.anchor);

    // TAB 2: HOME LIGHTING
    const tab_lighting = new Tab("lighting", false);
    tab_lighting.set_title("Home Lighting");
    content.appendChild(tab_lighting.html);
    tab_anchors.appendChild(tab_lighting.anchor);

    // initialize all tabs and their inner content
    init_tab_overview(tab_overview);
    init_tab_lighting(tab_lighting);
}


// ============================== Overview Tab ============================== //
// Initializes the given tab as the main "overview" tab.
function init_tab_overview(tab_overview)
{
    // add an initial card to the overview tab
    const card_main = new Card("card_main");
    card_main.set_title("Welcome");
    tab_overview.add_card(card_main);
    init_card_main(card_main);

    // set up a card for third-party services my server is running
    const card_servs_tp = new Card("card_services_tp");
    card_servs_tp.set_title("Services");
    tab_overview.add_card(card_servs_tp);
    const card_servs_tp_desc = document.createElement("p");
    card_servs_tp_desc.innerHTML = "These third-party services are running " +
                                   "on the home server.";
    card_servs_tp.add_html(card_servs_tp_desc);
    init_service_card(card_servs_tp, services, false);

    // set up a card for homemade services my server is running
    const card_servs_me = new Card("card_services_me");
    card_servs_me.set_title("My Services");
    tab_overview.add_card(card_servs_me);
    const card_servs_me_desc = document.createElement("p");
    card_servs_me_desc.innerHTML = "These homemade services were created by " +
                                   "me for the home server.";
    card_servs_me.add_html(card_servs_me_desc);
    init_service_card(card_servs_me, services_me, true);

    // refresh the services
    refresh_services(services, false);
    refresh_services(services_me, true);
}

// Initializes the main card on the home page.
function init_card_main(card_main)
{
    // add introductory text to the card
    const p1 = document.createElement("p");
    p1.innerHTML = "This is my home server. It's under construction.";
    card_main.add_html(p1);
}

// Takes in a MDL card and a list of Service objects and initializes it to
// display each service.
function init_service_card(card, servs, homemade)
{
    // create a table to contain all services
    const table = document.createElement("table");
    table.className = "mdl-data-table mdl-js-data-table mdl-shadow--2dp " +
                      "mdl-color--grey-900";
    table.style.cssText = "width: 100%;";
    table.id = card.id + "_service_table";
    card.add_html(table);

    // add a header row to describe the entries
    const hrow = document.createElement("tr");
    const hcols = [
        ["Name", NaN],
        ["Port", 0],
        ["Status", NaN]
    ];
    if (homemade)
    { hcols.push(["Auth", NaN]); }
    for (let i = 0; i < hcols.length; i++)
    {
        const th = document.createElement("th");
        th.className = "mdl-color-text--grey-100";
        if (isNaN(hcols[i][1]))
        { th.className += " mdl-data-table__cell--non-numeric"; }
        th.innerHTML = hcols[i][0];
        hrow.appendChild(th);
    }
    table.appendChild(hrow);

    // for each service, add a row to the table
    for (let i = 0; i < servs.length; i++)
    {
        const s = servs[i];
        const row = document.createElement("tr");
        
        // create a span containing an icon PLUS the service name
        const namespan = document.createElement("span");
        namespan.appendChild(s.make_icon());
        namespan.innerHTML += " " + s.name;

        // ping the service to determine if it's up, then create an appropriate
        // status icon
        status_icon = document.createElement("i");
        status_icon.className = css_loading;
        status_icon.id = s.id + "_status_icon";
        
        // construct an array of values to be stored in the row's columns
        const cols = [
            namespan,
            s.url.port,
            status_icon
        ];
        if (homemade)
        {
            // add a column for authentication
            const auth_icon = document.createElement("i");
            auth_icon.className = css_loading;
            auth_icon.id = s.id + "_auth_icon";
            
            // create a button to contain the icon
            const auth_button = document.createElement("button");
            auth_button.className = "mdl-button mdl-js-button mdl-button--raised mdl-js-ripple-effect";
            auth_button.className += " mdl-color--grey-800 mdl-color-text--grey-200";
            auth_button.style.cssText = "text-align: center";
            auth_button.id = s.id + "_auth_button";
            auth_button.setAttribute("onclick", "authenticate_service(\"" + s.id + "\")");
            auth_button.appendChild(auth_icon);
            cols.push(auth_button);
        }

        // for each column add a 'td' element
        for (let j = 0; j < cols.length; j++)
        {
            const td = document.createElement("td");
            const c = cols[j];
            if (isNaN(c))
            { td.className = "mdl-data-table__cell--non-numeric"; }

            // check the column's value and append accordingly
            if (c instanceof Element)
            { td.appendChild(c); }
            else
            { td.innerHTML = c; }
            row.appendChild(td);
        }
        table.appendChild(row);
    }
}

// Pings the list of given services and updates their status icons.
function refresh_services(servs, homemade)
{
    for (let i = 0; i < servs.length; i++)
    {
        const s = servs[i];

        // ping the service to determine if it's online or not
        const status_icon = document.getElementById(s.id + "_status_icon");
        let online = false;
        s.ping().then(
            function(result)
            {
                if (result)
                {
                    // the service is online, so we'll mark it as such
                    status_icon.className = css_check;

                    // if the service is homemade, we'll do an addition check to
                    // determine if the user is authenticated
                    if (homemade)
                    {
                        const auth_icon = document.getElementById(s.id + "_auth_icon");
                        const auth_button = document.getElementById(s.id + "_auth_button");
                        s.auth_check().then(
                            function(result)
                            {
                                if (result.success)
                                {
                                    auth_icon.className = css_check;
                                    button_disable(auth_button);
                                }
                                else
                                {
                                    auth_icon.className = css_xmark;
                                    button_enable(auth_button);
                                }
                            },
                            function()
                            { auth_icon.className = css_xmark; }
                        );
                    }
                }
                else
                { status_icon.className = css_xmark; }
            },
            function()
            { status_icon.className = css_xmark; }
        );
    }
}

// Invoked when a button is clicked to authenticate with a particular service.
async function authenticate_service(service_id)
{
    // find the homemade service with the matching ID
    let s = null;
    for (let i = 0; i < services_me.length; i++)
    {
        if (services_me[i].name.toLowerCase() == service_id.toLowerCase())
        {
            s = services_me[i];
            break;
        }
    }
    
    // if a service wasn't found, return
    if (s == null)
    { return; }

    // otherwise, prompt the user for a username and password
    const username = prompt("Please enter your username for " + s.name + ":");
    if (username == "")
    {
        const dlg = new Dialog(s.id + "_auth_error_dialog1");
        dlg.set_title("Input Error");
        dlg.set_message("Your username cannot be blank.");
        dlg.add_action("OK", dlg.close);
        const result = dlg.show();
        return;
    }
    const password = prompt("Please enter your password for " + s.name + ":");
    if (password == "")
    {
        const dlg = new Dialog(s.id + "_auth_error_dialog2");
        dlg.set_title("Input Error");
        dlg.set_message("Your password cannot be blank.");
        dlg.add_action("OK", dlg.close);
        const result = dlg.show();
        return;
    }

    // contact the service via HTTP and attempt to authenticate
    const result = await s.auth_login(username, password);

    // show the user the result with another dialog
    const dlg = new Dialog(s.id + "_auth_success_dialog");
    dlg.set_title("Success");
    if (!result.success)
    { dlg.set_title("Failure"); }
    dlg.set_message(result.message);
    dlg.add_action("OK", dlg.close);
    dlg.show();
    
    // refresh the services to display any visual updates after the attempt
    refresh_services(services_me, true);
}


// ============================== Lighting Tab ============================== //
// Initializes the lighting tab and everything within.
async function init_tab_lighting(tab_lighting)
{
    // iterate through my services to find the lumen service
    for (let i = 0; i < services_me.length; i++)
    {
        if (services_me[i].name.toLowerCase() == "lumen")
        { 
            lumen = services_me[i];
            break;
        }
    }

    // if the lumen service couldn't be found, complain and return
    if (lumen === null)
    {
        console.log("Couldn't find the lumen service.");
        return
    }

    // make sure we're authenticated with lumen
    const result = await lumen.auth_check();
    if (!result.success)
    {
        const card = new Card("card_lights_noauth");
        card.set_title("Not Authenticated");
        tab_lighting.add_card(card);

        const p = document.createElement("p");
        p.innerHTML = "You aren't authenticated with Lumen. " +
                      "If you just logged in, refresh the page.";
        card.add_html(p);
        return;
    }

    // initialize a Lumen object and get all light information
    lumen = new Lumen(lumen);
    lights = await lumen.get_lights();

    // if no lights are returned, make a card to tell the user
    if (lights.length == 0)
    {
        const card = new Card("card_lights_none");
        card.set_title("No Lights");
        tab_lighting.add_card(card);

        const p = document.createElement("p");
        p.innerHTML = "Lumen reported zero connected lights.";
        card.add_html(p);
        return;
    }

    // for each light, we'll create an interactive card
    for (let i = 0; i < lights.length; i++)
    {
        l = lights[i];
        const card = new Card("card_light_" + l.id);

        // build an icon and title for the light's card
        let icon_class = lumen_icon_off_class;
        if (l.status.power)
        { icon_class = lumen_icon_on_class; }
        const icon = "<i id=\"" + "lumenicon_" + l.id + "\" class=\"" + icon_class + "\"></i> ";;
        card.set_title(icon + l.id);
        tab_lighting.add_card(card);

        const p = document.createElement("p");
        p.innerHTML = l.description;
        card.add_html(p);

        // add a few actions to the card
        card.add_action("lumenon_" + l.id, "ON", light_turn_on);
        card.add_action("lumenoff_" + l.id, "OFF", light_turn_off);

        // if the light supports color, add a color selector
        if (l.has_color)
        {
            const d = document.createElement("div");

            const cp = document.createElement("p");
            cp.innerHTML = "<b>Color</b>";
            
            const d2 = document.createElement("div");
            d2.style.cssText = "text-align: center";
            const ci = document.createElement("input");
            ci.type = "color";
            ci.id = "lumencolor_" + l.id;
            ci.style.cssText = "width: 90%; height: 100px; border-color; black; margin: 16px; padding: 0;";
            d2.appendChild(ci);

            // split the RGB string and conver it to hex
            const cpieces = l.status.color.split(",");
            const cval = {
                "r": parseInt(cpieces[0]),
                "g": parseInt(cpieces[1]),
                "b": parseInt(cpieces[2])
            };
            ci.value = "#" + rgb_to_hex(cval);

            d.appendChild(cp);
            d.appendChild(d2);
            card.add_html(d);
        }

        // if the light supports brightness, add a brightness slider
        if (l.has_brightness)
        {
            const d = document.createElement("div");

            const bp = document.createElement("p");
            bp.innerHTML = "<b>Brightness</b>";
            
            const d2 = document.createElement("div");
            d2.style.cssText = "text-align: center";
            const bi = document.createElement("input");
            bi.type = "range"
            bi.id = "lumenbrightness_" + l.id;
            bi.className = "mdl-slider mdl-js-slider";
            bi.style.cssText = "margin: 16px";
            bi.min = 0;
            bi.max = 100;
            bi.step = 1;
            const light_value = l.status.brightness * 100.0;
            bi.value = light_value;
            d2.appendChild(bi);

            d.appendChild(bp);
            d.appendChild(d2);
            card.add_html(d);
        }
    }
}

// Click event for a light's "ON" button.
async function light_turn_on(ev)
{
    const btn = ev.currentTarget;
    button_disable(btn);

    // extract the light ID from the button ID and find the light
    let lid = ev.currentTarget.id;
    lid = lid.replace("lumenon_", "");
    let light = lumen.search_light(lid);
    if (!light)
    {
        console.log("Failed to find light: \"" + lid + "\".");
        return;
    }

    // extract color from the card, if applicable
    color = null;
    if (light.has_color)
    {
        const cinput = document.getElementById("lumencolor_" + lid);
        if (cinput !== null)
        {
            // parse the input's value into a "R,G,B" string
            rgb = hex_to_rgb(cinput.value);
            color = "" + rgb.r + "," + rgb.g + "," + rgb.b
        }
    }

    // extract brightness from the card, if applicable
    brightness = null;
    if (light.has_brightness)
    {
        const binput = document.getElementById("lumenbrightness_" + lid);
        if (binput !== null)
        {
            // convert the string value into a float from 0.0-1.0
            brightness = parseFloat(binput.value) / 100.0;
        }
    }

    await lumen.turn_on(lid, color, brightness);
    button_enable(btn);

    // set the light's icon css to update the change
    const icon = document.getElementById("lumenicon_" + lid);
    icon.className = lumen_icon_on_class;
}

// Click event for a light's "OFF" button.
async function light_turn_off(ev)
{
    const btn = ev.currentTarget;
    button_disable(btn);

    // extract the light ID from the button ID and find the light object
    let lid = ev.currentTarget.id;
    lid = lid.replace("lumenoff_", "");
    let light = lumen.search_light(lid);
    if (!light)
    {
        console.log("Failed to find light: \"" + lid + "\".");
        return;
    }

    await lumen.turn_off(lid);
    button_enable(btn);
    
    // set the light's icon css to update the change
    const icon = document.getElementById("lumenicon_" + lid);
    icon.className = lumen_icon_off_class;
}


