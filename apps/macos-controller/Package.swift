// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "DeutschOSController",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "DeutschOSController", targets: ["DeutschOSController"])
    ],
    targets: [
        .executableTarget(name: "DeutschOSController"),
    ]
)
