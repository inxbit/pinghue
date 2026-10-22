# typed: false
# frozen_string_literal: true

# Resource blocks are sourced from the published PyPI sdist closure.

require "socket"

class Pinghue < Formula
  include Language::Python::Virtualenv

  desc "Colored, concurrent ICMP/TCP ping monitor for the terminal"
  homepage "https://github.com/inxbit/pinghue"
  url "https://files.pythonhosted.org/packages/4a/68/ce501359462234bac4c2733d5bf66e26b9b2bd6999052a647c953fd828fe/pinghue-5.1.0.tar.gz"
  sha256 "e5deb323cddd2d3c246ba830669e0261f9ae0b102041ca828097d87967b3766c"
  license "MIT"
  head "https://github.com/inxbit/pinghue.git", branch: "main"

  depends_on "python@3.13"

  resource "icmplib" do
    url "https://files.pythonhosted.org/packages/6d/78/ca07444be85ec718d4a7617f43fdb5b4eaae40bc15a04a5c888b64f3e35f/icmplib-3.0.4.tar.gz"
    sha256 "57868f2cdb011418c0e1d5586b16d1fabd206569fe9652654c27b6b2d6a316de"
  end

  resource "linkify-it-py" do
    url "https://files.pythonhosted.org/packages/2e/c9/06ea13676ef354f0af6169587ae292d3e2406e212876a413bf9eece4eb23/linkify_it_py-2.1.0.tar.gz"
    sha256 "43360231720999c10e9328dc3691160e27a718e280673d444c38d7d3aaa3b98b"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz"
    sha256 "04a21681d6fbb623de53f6f364d352309d4094dd4194040a10fd51833e418d49"
  end

  resource "mdit-py-plugins" do
    url "https://files.pythonhosted.org/packages/59/fc/f8d0863f8862f25602c0404d75568e89fb6b4109804645e5cdfb1be5cf56/mdit_py_plugins-0.6.1.tar.gz"
    sha256 "a2bca0f039f39dbd35fb74ae1b5f998608c437463371f0ff7f49a19a17a114d0"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "platformdirs" do
    url "https://files.pythonhosted.org/packages/b8/d7/e7bfbc86e9f99ff7807e24de7703f032e9c9ba80bb355cf26e0e9bc5a75e/platformdirs-4.11.3.tar.gz"
    sha256 "66a73d38a849810252df809a3d8bcbda8e26f6c189920e7535ad608a48dbb5ab"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/c3/b2/bc9c9196916376152d655522fdcebac55e66de6603a76a02bca1b6414f6c/pygments-2.20.0.tar.gz"
    sha256 "6757cd03768053ff99f3039c1a36d6c0aa0b263438fcab17520b30a303a82b5f"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/c0/8f/0722ca900cc807c13a6a0c696dacf35430f72e0ec571c4275d2371fca3e9/rich-15.0.0.tar.gz"
    sha256 "edd07a4824c6b40189fb7ac9bc4c52536e9780fbbfbddf6f1e2502c31b068c36"
  end

  resource "textual" do
    url "https://files.pythonhosted.org/packages/00/21/39a76b01bd5eea82a04baaca7580e105d8c59450df03998345bb2cfb307b/textual-8.2.8.tar.gz"
    sha256 "3f106a9fbc73e39dd266c9712432087de78a6d644084c7c241d6a25c3169115b"
  end

  resource "typing-extensions" do
    url "https://files.pythonhosted.org/packages/f6/cc/6253133b5bb138fc3306cebfbda2c520f545d36b5be2c7255cc528bb45d6/typing_extensions-4.16.0.tar.gz"
    sha256 "dc983d19a509c94dba722ee6abd33940f7c05a89e243c47e907eb4db6f1a43e5"
  end

  resource "uc-micro-py" do
    url "https://files.pythonhosted.org/packages/78/67/9a363818028526e2d4579334460df777115bdec1bb77c08f9db88f6389f2/uc_micro_py-2.0.0.tar.gz"
    sha256 "c53691e495c8db60e16ffc4861a35469b0ba0821fe409a8a7a0a71864d33a811"
  end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    on_linux do
      <<~EOS
        ICMP mode on Linux needs one of:

          (A) Allow unprivileged ICMP for your group (recommended):
                gid="$(id -g)"
                sudo sysctl -w "net.ipv4.ping_group_range=${gid} ${gid}"
              Persist it:
                echo "net.ipv4.ping_group_range=${gid} ${gid}" \\
                  | sudo tee /etc/sysctl.d/99-pinghue.conf

              The broader range 0 2147483647 also works, but enables
              unprivileged ICMP for every local group.

          (B) Skip ICMP entirely and use TCP mode:
                pinghue -p 443 example.com

        Note: setcap cap_net_raw does not work here — the pinghue command is
        a Python launcher script, and Linux ignores file capabilities on
        interpreter scripts.

        Run `pinghue --check` to diagnose your environment.
      EOS
    end
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/pinghue --version")
    assert_match "pinghue", shell_output("#{bin}/pinghue --help")
    server = TCPServer.new("127.0.0.1", 0)
    port = server.addr[1].to_s
    pid = fork do
      loop do
        client = server.accept
        client.close
      end
    end

    begin
      system bin/"pinghue", "-p", port, "127.0.0.1", "-c", "1", "--no-tui", "--fail-on-down"
    ensure
      Process.kill("TERM", pid)
      Process.wait(pid)
      server.close
    end
  end
end
