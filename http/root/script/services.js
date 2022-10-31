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
        // create a URL and set it to "no-cors" mode to avoid the browser error
        // where the server doesn't specify an "Access-Control-Allow-Origin"
        // header without authentication. This 'ping' function is really just
        // supposed to see if the service is up, so we don't really care about
        // security too much here.
        const url = new URL(this.url.address, this.url.port, "/");
        url.no_cors = true;
        
        // send the request
        let resp = null;
        try
        { resp = await url.send_request("GET", null); }
        catch
        { resp = null; }
        
        // if a response object was retrieved from above, we successfully got
        // through to the service. So, it must be up
        return resp instanceof Response;
    }
    
    // Sends a HTTP GET request to the authentication-checker endpoint to see if
    // the user is logged into the service. Returns a boolean indicating whether
    // or not the user is authenticated.
    async auth_check()
    {
        const url = new URL(this.url.address, this.url.port, "/auth/check");

        let resp = null;
        try
        { resp = await url.send_request("GET", null); }
        catch
        { resp = null; }

        // if the response is null, return null
        if (resp == null)
        { return {success: false, message: "Failed to send the request"}; }
        
        // parse the response as JSON and find the set-cookie header if the
        // request succeeded
        const jdata = JSON.parse(await resp.text());
        return jdata;
    }
    
    // Sends a HTTP POST request to the login endpoint for the service.
    async auth_login(username, password)
    {
        const url = new URL(this.url.address, this.url.port, "/auth/login");

        let resp = null;
        const login_data = {"username": username, "password": password};
        try
        { resp = await url.send_request("POST", login_data); }
        catch
        { resp = null; }
        
        // if the response is null, return null
        if (resp == null)
        { return {success: false, message: "Failed to send the request"}; }

        // parse the response JSON and return it
        const jdata = JSON.parse(await resp.text());
        return jdata;
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
        i.style.cssText = "margin: 8px;"
        return i;
    }
}

