﻿using System;
using Steamworks;

SteamClient.Init(887570U, true);
if (SteamClient.IsValid)
{
    SteamClient.RunCallbacks();
    var poller = new LobbyWatcher();
    poller.Start();
}
else
{
    Console.WriteLine("Failed to initialize Steam Client");
}
