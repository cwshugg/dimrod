// This module defines the 'Service' class, a simple uniform way for my server
// to keep track of various services the home server provides.

// Main service class.
class Service
{
    // Constructor. Takes in an ID, name, URL object, and a few other extra
    // fields.
    constructor(id, name, url, fa_icon, color)
    {
        this.id = id;
        this.name = name;
        this.url = url;
        this.fa_icon = fa_icon;
        this.color = color
    }


    // =========================== Communication ============================ //
    // Simple function that sends a HTTP request to the service and returns
    // whether or not a response was received.
    async ping()
    {
        const url = new URL(this.url.address, this.url.port, "/");
        let resp = null;
        try
        { resp = await url.send_request("GET", null); }
        catch
        { resp = null; }
        
        // if a response object was retrieved from above, we successfully got
        // through to the service. So, it must be up
        return resp instanceof Response;
    }


    // =========================== HTML Elements ============================ //
    // Creates and returns an HTML anchor element that takes the user to the
    // running service.
    make_anchor(text)
    {
        const a = document.createElement("a");
        a.id = this.id + "_anchor";
        a.href = this.url.get_string();
        a.innerHTML = text;
        return a;
    }
    
    // Creates and returns a font-awesome icon element for the service.
    make_icon()
    {
        const i = document.createElement("i");
        i.id = this.id + "_icon";
        i.className = this.fa_icon + " " + this.color;
        return i;
    }
}

