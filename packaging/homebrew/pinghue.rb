# typed: false
# frozen_string_literal: true

# Resource blocks are sourced from the published PyPI sdist closure.

require "socket"

class Pinghue < Formula
  include Language::Python::Virtualenv

  desc "Colored, concurrent ICMP/TCP ping monitor for the terminal"
  homepage "https://github.com/inxbit/pinghue"
  url "https://files.pythonhosted.org/packages/0c/13/e1e2b494c86ada2c29d6c902a3f11a1ade72ef0568f5e000f33b9cc68e2f/pinghue-8.0.3.tar.gz"
  sha256 "ae51eb8de2e710c53383a071983366180b40c0fbc7e0b9e29426058b1f5d7a59"
  license "MIT"
  head "https://github.com/inxbit/pinghue.git", branch: "main"

  depends_on "python@3.13"

  resource "linkify-it-py" do
    url "https://files.pythonhosted.org/packages/45/98/7a1a5f31fd5c7ba93e963b168e244b8e3dd705b3d2a718e3c3307583bf57/linkify_it_py-2.2.0.tar.gz"
    sha256 "907acd2d17ac1fbb9ddb62c8957ccbd6158cac602231a15c3b0cd1e215f03cee"
  end

  resource "markdown-it-py" do
    url "https://files.pythonhosted.org/packages/16/fc/cee2c45553c9a9bf77d8b0c391560c556d7ed756b1f0bb35eb5d48db09ea/markdown_it_py-4.3.0.tar.gz"
    sha256 "91a58fa5b72c71a40cf7177250fd767480d7a2bb3ac426ccfb76fb90305fdaaa"
  end

  resource "mdit-py-plugins" do
    url "https://files.pythonhosted.org/packages/ad/a4/77a4e1be19fd9b08993c76d5e54979ac836b327754f1f5fecfed1b56cc67/mdit_py_plugins-0.7.0.tar.gz"
    sha256 "26d2179227aaf4d1dc7acffe7a738cbd15cade6b5a61cff0a2cbf9fed93efede"
  end

  resource "mdurl" do
    url "https://files.pythonhosted.org/packages/d6/54/cfe61301667036ec958cb99bd3efefba235e65cdeb9c84d24a8293ba1d90/mdurl-0.1.2.tar.gz"
    sha256 "bb413d29f5eea38f31dd4754dd7377d4465116fb207585f97bf925588687c1ba"
  end

  resource "platformdirs" do
    url "https://files.pythonhosted.org/packages/71/18/0b0d99f5bf164f04a7a9534c3dbdbe4d5fd2b382667685ad40c192c4d3d1/platformdirs-4.14.8.tar.gz"
    sha256 "96893d686e84e9e6f358a9ea41a8de544df66dbf2ba6cda250e7dc73acf85b9b"
  end

  resource "pygments" do
    url "https://files.pythonhosted.org/packages/49/2e/ced460408999b33da6b31b0021b0f37d329e202d4169aeb164493778f25b/pygments-2.21.0.tar.gz"
    sha256 "610ca751c9bc2492b38eb9a38a7fbc93edbbb2d7182edaf34e66ae493dee5c8c"
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
