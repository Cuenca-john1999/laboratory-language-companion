// swift-tools-version: 5.9

import PackageDescription

let package = Package(
    name: "LLCController",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "LLCController", targets: ["LLCController"])
    ],
    targets: [
        .executableTarget(name: "LLCController"),
    ]
)
